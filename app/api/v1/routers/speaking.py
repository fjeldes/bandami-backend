from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from uuid import UUID
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.exam import Exam, Evaluation
from app.schemas.evaluation import EvaluationResponse, ExamCreate, ExamResponse
from app.core.auth import (
    get_current_user,
    get_user_plan_info,
    check_daily_limit,
    get_ai_provider,
    compute_feedback_unlocks_at,
)
from app.services.storage import upload_audio_bytes, download_audio_bytes

import logging
import asyncio
logger = logging.getLogger("ielts.speaking")
from app.core.limiter import limiter
from app.services.providers.base import SpeakingEvaluator, SPEAKING_CRITERIA_KEYS, ProviderUnavailableError
from datetime import datetime, timezone

router = APIRouter()


def _get_user_friendly_error(e: Exception, exam_id: str) -> tuple[int, str]:
    msg = str(e).lower()
    if "rate_limit" in msg or "429" in msg or "tpm" in msg or "rpm" in msg:
        return 503, "AI service is temporarily busy due to high demand. Please wait a moment and try again."
    if "too large" in msg or "413" in msg or "token" in msg or "length" in msg:
        return 413, "Your audio response is too long for the AI to process. Please try with a shorter recording."
    if "model" in msg and ("not found" in msg or "does not exist" in msg or "access" in msg):
        return 503, "AI service is currently unavailable. Please try again in a few minutes."
    if "timeout" in msg or "deadline" in msg or "connection" in msg:
        return 503, "AI service is taking too long to respond. Please try again."
    logger.exception("Evaluation failed for exam=%s error=%s", exam_id, str(e)[:500])
    return 503, "Evaluation failed. Please try again."


def _filter_speaking_criteria(criteria: dict, is_visible: bool) -> dict:
    """Free tier: return main 4 criteria with scores and comments. Premium: all criteria."""
    if is_visible:
        return criteria
    return {k: v for k, v in criteria.items() if k in SPEAKING_CRITERIA_KEYS}


def _is_provider_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(kw in msg for kw in [
        "timeout", "unavailable", "connection", "503", "500",
        "retry", "deadline", "429", "service", "reset",
        "overloaded", "capacity", "exhausted", "empty",
    ])


@router.post("/exam", response_model=ExamResponse)
async def create_speaking_exam(
    body: ExamCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(Exam).filter(
        Exam.user_id == user_id,
        Exam.exam_type == "speaking",
        Exam.status == "pending",
    ).first()

    if existing:
        if body.question_id and str(existing.question_id) != str(body.question_id):
            existing.question_id = body.question_id
            db.commit()
        return ExamResponse(
            id=str(existing.id),
            user_id=str(existing.user_id),
            question_id=str(existing.question_id) if existing.question_id else None,
            exam_type=existing.exam_type,
            task_type=existing.task_type,
            status=existing.status,
            attempt_number=existing.attempt_number,
            eval_source=existing.eval_source,
            created_at=existing.created_at,
        )

    exam = Exam(
        user_id=user_id,
        question_id=str(body.question_id) if body.question_id else None,
        exam_type="speaking",
        task_type=None,
        status="pending",
        attempt_number=body.attempt_number,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)

    return ExamResponse(
        id=str(exam.id),
        user_id=str(exam.user_id),
        question_id=str(exam.question_id) if exam.question_id else None,
        exam_type=exam.exam_type,
        task_type=exam.task_type,
        status=exam.status,
        attempt_number=exam.attempt_number,
        eval_source=exam.eval_source,
        created_at=exam.created_at,
    )


@router.post("/", response_model=EvaluationResponse)
@limiter.limit("5/minute")
async def evaluate_speaking_endpoint(
    request: Request,
    exam_id: str = Form(...),
    audio: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(check_daily_limit),
    provider: SpeakingEvaluator = Depends(get_ai_provider),
):
    exam = db.query(Exam).filter(
        Exam.id == exam_id,
        Exam.user_id == user_id,
    ).first()

    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    if exam.status not in ("pending", "failed"):
        raise HTTPException(status_code=400, detail="Exam already processed")
    if exam.status == "failed":
        db.query(Evaluation).filter(Evaluation.exam_id == exam.id).delete()
        exam.status = "pending"
        db.commit()

    is_free = plan_info.get("tier", "free") != "premium"
    delay_hours = plan_info.get("feedback_delay_hours", 0)
    unlocks_at = compute_feedback_unlocks_at(delay_hours)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    exam.status = "processing"
    exam.eval_source = plan_info.get("eval_source", "free")
    db.commit()

    # Store audio in GCS first — always persist it before any AI call
    try:
        audio_bytes = await audio.read()
        
        # Validate file size (max 25MB)
        if len(audio_bytes) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Audio file too large. Maximum size is 25MB.")
        
        # Validate MIME type
        allowed_mime = {"audio/webm", "audio/mpeg", "audio/mp4", "audio/wav", "audio/ogg"}
        if audio.content_type and audio.content_type not in allowed_mime:
            raise HTTPException(status_code=415, detail=f"Unsupported audio format: {audio.content_type}. Use WebM, MP3, MP4, WAV, or OGG.")
        
        await asyncio.to_thread(upload_audio_bytes, str(exam.id), audio_bytes, audio.content_type or "audio/webm")
        
        transcribe_provider = provider
        try:
            transcription = await provider.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
        except Exception as e:
            if _is_provider_error(e):
                fb_name = plan_info.get("fallback_provider")
                if fb_name:
                    logger.warning("Primary transcription failed for exam=%s provider=%s error=%s, trying fallback=%s", exam.id, provider.provider_name, str(e)[:200], fb_name)
                    from app.services.providers import get_provider
                    fb = get_provider(fb_name)
                    transcribe_provider = fb
                    transcription = await fb.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
                else:
                    raise ProviderUnavailableError(str(e)) from e
            else:
                raise

        eval_provider = provider
        try:
            result = await provider.evaluate_speaking(transcription, detailed=not is_free)
        except ProviderUnavailableError:
            fb_name = plan_info.get("fallback_provider")
            if fb_name:
                logger.warning("Primary evaluation unavailable for exam=%s provider=%s, trying fallback=%s", exam.id, provider.provider_name, fb_name)
                from app.services.providers import get_provider
                fb = get_provider(fb_name)
                eval_provider = fb
                result = await fb.evaluate_speaking(transcription, detailed=not is_free)
            else:
                raise
        except Exception as e:
            if _is_provider_error(e):
                fb_name = plan_info.get("fallback_provider")
                if fb_name:
                    logger.warning("Primary evaluation error for exam=%s provider=%s error=%s, trying fallback=%s", exam.id, provider.provider_name, str(e)[:200], fb_name)
                    from app.services.providers import get_provider
                    fb = get_provider(fb_name)
                    eval_provider = fb
                    result = await fb.evaluate_speaking(transcription, detailed=not is_free)
                else:
                    raise ProviderUnavailableError(str(e)) from e
            else:
                raise

        ev = Evaluation(
            exam_id=exam.id,
            user_submission=transcription,
            overall_band=result.overall_band,
            criteria_scores=result.criteria_scores,
            general_feedback=result.general_feedback,
            detailed_feedback=result.detailed_feedback,
            grammar_corrections=result.grammar_corrections,
            provider_used=eval_provider.provider_name,
            ai_model_used=result.model,
            tokens_used=result.tokens,
            processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at,
        )
        db.add(ev)
        db.flush()  # populate id, created_at before building Response

        exam.status = "completed"
        exam.completed_at = datetime.now(timezone.utc)

        # Validate response before committing — if Pydantic fails, rollback prevents orphan Evaluation
        eval_response = EvaluationResponse(
            id=str(ev.id),
            exam_id=str(ev.exam_id),
            user_submission=transcription,
            overall_band=ev.overall_band,
            criteria_scores=_filter_speaking_criteria(ev.criteria_scores, is_visible),
            general_feedback=result.general_feedback or "",
            detailed_feedback=result.detailed_feedback if is_visible else None,
            grammar_corrections=result.grammar_corrections if is_visible else [],
            provider_used=eval_provider.provider_name,
            ai_model_used=result.model,
            tokens_used=result.tokens,
            processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at,
            is_feedback_visible=is_visible,
            created_at=ev.created_at,
            exam_status=exam.status,
        )

        db.commit()

        # Update last_active_at
        from app.models.user import UserProfile
        db.query(UserProfile).filter(UserProfile.id == user_id).update({UserProfile.last_active_at: datetime.now(timezone.utc)})
        db.commit()

        logger.info("Evaluation completed exam=%s user=%s tier=%s eval_source=%s band=%s transcribe=%s eval=%s",
                    exam.id, user_id, plan_info.get("tier"), plan_info.get("eval_source"), ev.overall_band,
                    transcribe_provider.provider_name, eval_provider.provider_name)

        return eval_response

    except ProviderUnavailableError as e:
        logger.warning("Provider unavailable for exam=%s tier=%s eval_source=%s primary=%s fallback=%s error=%s",
                       exam.id, plan_info.get("tier"), plan_info.get("eval_source"),
                       provider.provider_name, plan_info.get("fallback_provider"), str(e)[:200])
        exam.status = "pending"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Our AI agent is currently experiencing high demand. Please try again later.",
        )
    except Exception as e:
        exam.status = "pending"
        exam.error_message = str(e)[:500]
        db.commit()
        status_code, detail = _get_user_friendly_error(e, str(exam.id))
        raise HTTPException(status_code=status_code, detail=detail)


@router.post("/{exam_id}/retry", response_model=EvaluationResponse)
async def retry_speaking_evaluation(
    exam_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(check_daily_limit),
    provider: SpeakingEvaluator = Depends(get_ai_provider),
):
    """Retry evaluation using previously saved audio from GCS (no re-recording needed)."""
    exam = db.query(Exam).filter(
        Exam.id == exam_id,
        Exam.user_id == user_id,
    ).first()

    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    if exam.status not in ("pending", "failed"):
        raise HTTPException(status_code=400, detail="Exam already processed")

    db.query(Evaluation).filter(Evaluation.exam_id == exam.id).delete()
    exam.status = "pending"
    db.commit()

    is_free = plan_info.get("tier", "free") != "premium"
    delay_hours = plan_info.get("feedback_delay_hours", 0)
    unlocks_at = compute_feedback_unlocks_at(delay_hours)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    exam.status = "processing"
    exam.eval_source = plan_info.get("eval_source", "free")
    db.commit()

    try:
        audio_bytes, content_type = download_audio_bytes(exam_id)

        transcribe_provider = provider
        try:
            transcription = await provider.transcribe_audio(audio_bytes, f"{exam_id}.webm")
        except Exception as e:
            if _is_provider_error(e):
                fb_name = plan_info.get("fallback_provider")
                if fb_name:
                    logger.warning("Primary transcription failed on retry for exam=%s provider=%s error=%s, trying fallback=%s", exam.id, provider.provider_name, str(e)[:200], fb_name)
                    from app.services.providers import get_provider
                    fb = get_provider(fb_name)
                    transcribe_provider = fb
                    transcription = await fb.transcribe_audio(audio_bytes, f"{exam_id}.webm")
                else:
                    raise ProviderUnavailableError(str(e)) from e
            else:
                raise

        eval_provider = provider
        try:
            result = await provider.evaluate_speaking(transcription, detailed=not is_free)
        except ProviderUnavailableError:
            fb_name = plan_info.get("fallback_provider")
            if fb_name:
                logger.warning("Primary evaluation unavailable on retry for exam=%s provider=%s, trying fallback=%s", exam.id, provider.provider_name, fb_name)
                from app.services.providers import get_provider
                fb = get_provider(fb_name)
                eval_provider = fb
                result = await fb.evaluate_speaking(transcription, detailed=not is_free)
            else:
                raise
        except Exception as e:
            if _is_provider_error(e):
                fb_name = plan_info.get("fallback_provider")
                if fb_name:
                    logger.warning("Primary evaluation error on retry for exam=%s provider=%s error=%s, trying fallback=%s", exam.id, provider.provider_name, str(e)[:200], fb_name)
                    from app.services.providers import get_provider
                    fb = get_provider(fb_name)
                    eval_provider = fb
                    result = await fb.evaluate_speaking(transcription, detailed=not is_free)
                else:
                    raise ProviderUnavailableError(str(e)) from e
            else:
                raise

        ev = Evaluation(
            exam_id=exam.id,
            user_submission=transcription,
            overall_band=result.overall_band,
            criteria_scores=result.criteria_scores,
            general_feedback=result.general_feedback,
            detailed_feedback=result.detailed_feedback,
            grammar_corrections=result.grammar_corrections,
            provider_used=eval_provider.provider_name,
            ai_model_used=result.model,
            tokens_used=result.tokens,
            processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at,
        )
        db.add(ev)
        db.flush()  # populate id, created_at before building Response

        exam.status = "completed"
        exam.completed_at = datetime.now(timezone.utc)

        eval_response = EvaluationResponse(
            id=str(ev.id),
            exam_id=str(ev.exam_id),
            user_submission=transcription,
            overall_band=ev.overall_band,
            criteria_scores=_filter_speaking_criteria(ev.criteria_scores, is_visible),
            general_feedback=result.general_feedback or "",
            detailed_feedback=result.detailed_feedback if is_visible else None,
            grammar_corrections=result.grammar_corrections if is_visible else [],
            provider_used=eval_provider.provider_name,
            ai_model_used=result.model,
            tokens_used=result.tokens,
            processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at,
            is_feedback_visible=is_visible,
            created_at=ev.created_at,
            exam_status=exam.status,
        )

        db.commit()

        from app.models.user import UserProfile
        db.query(UserProfile).filter(UserProfile.id == user_id).update({UserProfile.last_active_at: datetime.now(timezone.utc)})
        db.commit()

        logger.info("Retry evaluation completed exam=%s user=%s tier=%s band=%s transcribe=%s eval=%s",
                    exam.id, user_id, plan_info.get("tier"), ev.overall_band,
                    transcribe_provider.provider_name, eval_provider.provider_name)

        return eval_response

    except ProviderUnavailableError as e:
        logger.warning("Provider unavailable on retry for exam=%s tier=%s primary=%s fallback=%s error=%s",
                       exam.id, plan_info.get("tier"),
                       provider.provider_name, plan_info.get("fallback_provider"), str(e)[:200])
        exam.status = "pending"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Our AI agent is currently experiencing high demand. Please try again later.",
        )
    except FileNotFoundError:
        exam.status = "pending"
        db.commit()
        raise HTTPException(status_code=404, detail="Audio no longer available for this exam.")
    except Exception as e:
        exam.status = "pending"
        exam.error_message = str(e)[:500]
        db.commit()
        status_code, detail = _get_user_friendly_error(e, str(exam.id))
        raise HTTPException(status_code=status_code, detail=detail)


@router.get("/{exam_id}/evaluation", response_model=EvaluationResponse)
async def get_speaking_evaluation(
    exam_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.user_id == user_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    ev = db.query(Evaluation).filter(Evaluation.exam_id == exam_id).first()
    if not ev:
        now = datetime.now(timezone.utc)
        is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)
        return EvaluationResponse(
            id=UUID(int=0),
            exam_id=UUID(exam_id) if isinstance(exam_id, str) else exam_id,
            user_submission="",
            overall_band=None,
            criteria_scores={},
            general_feedback=None,
            detailed_feedback=None,
            grammar_corrections=[],
            provider_used="gemini",
            ai_model_used=None,
            tokens_used=None,
            processing_time_ms=None,
            feedback_unlocks_at=now,
            is_feedback_visible=is_visible,
            upgraded_text=None,
            created_at=now,
            exam_status=exam.status,
        )

    unlocks_at = ev.feedback_unlocks_at

    now = datetime.now(timezone.utc)
    if unlocks_at and unlocks_at.tzinfo is None:
        unlocks_at = unlocks_at.replace(tzinfo=timezone.utc)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    return EvaluationResponse(
        id=str(ev.id),
        exam_id=str(ev.exam_id),
        user_submission=ev.user_submission,
        prompt_text=exam.question.prompt_text if exam.question else None,
        overall_band=ev.overall_band,
        criteria_scores=_filter_speaking_criteria(ev.criteria_scores, is_visible),
        general_feedback=ev.general_feedback or "",
        detailed_feedback=ev.detailed_feedback if is_visible else None,
        grammar_corrections=ev.grammar_corrections if is_visible else [],
        provider_used=ev.provider_used,
        ai_model_used=ev.ai_model_used,
        tokens_used=ev.tokens_used,
        processing_time_ms=ev.processing_time_ms,
        feedback_unlocks_at=unlocks_at or now,
        is_feedback_visible=is_visible,
        upgraded_text=ev.upgraded_text,
        created_at=ev.created_at,
        exam_status=exam.status,
    )



