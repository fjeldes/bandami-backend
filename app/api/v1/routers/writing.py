from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.exam import Exam, Evaluation
from app.schemas.evaluation import WritingSubmission, EvaluationResponse, ExamCreate, ExamResponse
from app.core.auth import (
    get_current_user,
    get_user_plan_info,
    check_daily_limit,
    get_ai_provider,
    compute_feedback_unlocks_at,
)
from app.core.limiter import limiter
from app.services.providers.base import WritingEvaluator, WRITING_CRITERIA_KEYS, ProviderUnavailableError
from datetime import datetime, timezone
import json
import logging

logger = logging.getLogger("ielts.writing")
router = APIRouter()


def _is_provider_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(kw in msg for kw in [
        "timeout", "unavailable", "connection", "503", "500",
        "retry", "deadline", "429", "service", "reset",
        "overloaded", "capacity", "exhausted", "empty",
    ])


def _filter_writing_criteria(criteria: dict, is_visible: bool) -> dict:
    """Free tier: return main 4 criteria scores only. Premium: all criteria."""
    if is_visible:
        return criteria
    return {k: v for k, v in criteria.items() if k in WRITING_CRITERIA_KEYS}


@router.post("/exam", response_model=ExamResponse)
async def create_writing_exam(
    body: ExamCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    exam = Exam(
        user_id=user_id,
        question_id=str(body.question_id) if body.question_id else None,
        exam_type="writing",
        task_type=body.task_type or "task2",
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
async def evaluate_writing_endpoint(
    request: Request,
    submission: WritingSubmission,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(check_daily_limit),
    provider: WritingEvaluator = Depends(get_ai_provider),
):
    exam = db.query(Exam).filter(
        Exam.id == str(submission.exam_id),
        Exam.user_id == user_id,
    ).first()

    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    if exam.status != "pending":
        raise HTTPException(status_code=400, detail="Exam already processed")

    is_free = plan_info.get("tier", "free") != "premium"
    delay_hours = plan_info.get("feedback_delay_hours", 0)
    unlocks_at = compute_feedback_unlocks_at(delay_hours)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    exam.status = "processing"
    exam.eval_source = plan_info.get("eval_source", "free")
    db.commit()

    try:
        task_type = exam.task_type or submission.task_type or "task2"
        try:
            result = await provider.evaluate_writing(submission.text, task_type, detailed=not is_free)
        except ProviderUnavailableError:
            fb_name = plan_info.get("fallback_provider")
            if fb_name:
                logger.info("Primary provider failed, trying fallback=%s", fb_name)
                from app.services.providers import get_provider
                fb = get_provider(fb_name)
                result = await fb.evaluate_writing(submission.text, task_type, detailed=not is_free)
            else:
                raise
        except Exception as e:
            if _is_provider_error(e):
                raise ProviderUnavailableError(str(e)) from e
            raise
            raise

        ev = Evaluation(
            exam_id=exam.id,
            user_submission=submission.text,
            overall_band=result.overall_band,
            criteria_scores=result.criteria_scores,
            general_feedback=result.general_feedback,
            detailed_feedback=result.detailed_feedback,
            grammar_corrections=result.grammar_corrections,
            provider_used=provider.provider_name,
            ai_model_used=result.model,
            tokens_used=result.tokens,
            processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at,
        )
        db.add(ev)

        exam.status = "completed"
        exam.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(ev)

        # Update last_active_at
        from app.models.user import UserProfile
        db.query(UserProfile).filter(UserProfile.id == user_id).update({UserProfile.last_active_at: datetime.now(timezone.utc)})
        db.commit()

        logger.info("Evaluation completed exam=%s user=%s tier=%s eval_source=%s band=%s",
                    exam.id, user_id, plan_info.get("tier"), plan_info.get("eval_source"), ev.overall_band)

        logger.info("EvalMetric provider=%s model=%s criteria=%d band=%s has_feedback=%s time_ms=%d tier=%s",
                    provider.provider_name, result.model or "unknown",
                    len(result.criteria_scores), ev.overall_band,
                    bool(result.general_feedback), result.processing_time_ms,
                    plan_info.get("tier", "free"))

        return EvaluationResponse(
            id=str(ev.id),
            exam_id=str(ev.exam_id),
            user_submission=submission.text,
            overall_band=ev.overall_band,
            criteria_scores=_filter_writing_criteria(ev.criteria_scores, is_visible),
            general_feedback=result.general_feedback or "",
            detailed_feedback=result.detailed_feedback if is_visible else None,
            grammar_corrections=result.grammar_corrections if is_visible else [],
            provider_used=provider.provider_name,
            ai_model_used=result.model,
            tokens_used=result.tokens,
            processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at,
            is_feedback_visible=is_visible,
            upgraded_text=ev.upgraded_text,
            created_at=ev.created_at,
        )

    except ProviderUnavailableError as e:
        logger.warning("Provider unavailable: %s tier=%s eval_source=%s", e, plan_info.get("tier"), plan_info.get("eval_source"))
        exam.status = "pending"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Our AI agent is currently experiencing high demand. Please try again later.",
        )

    except Exception as e:
        logger.exception("Evaluation failed for exam=%s user=%s tier=%s eval_source=%s", exam.id, user_id, plan_info.get("tier"), plan_info.get("eval_source"))
        exam.status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail="Evaluation failed. Please try again.")


@router.get("/{exam_id}/evaluation", response_model=EvaluationResponse)
async def get_writing_evaluation(
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
        raise HTTPException(status_code=404, detail="Evaluation not found")

    unlocks_at = ev.feedback_unlocks_at
    is_free = plan_info.get("tier", "free") != "premium"

    now = datetime.now(timezone.utc)
    if unlocks_at and unlocks_at.tzinfo is None:
        unlocks_at = unlocks_at.replace(tzinfo=timezone.utc)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    prompt_text = exam.question.prompt_text if exam.question else None

    return EvaluationResponse(
        id=str(ev.id),
        exam_id=str(ev.exam_id),
        user_submission=ev.user_submission,
        prompt_text=prompt_text,
        overall_band=ev.overall_band,
        criteria_scores=_filter_writing_criteria(ev.criteria_scores, is_visible),
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
    )


class UpgradeRequest(BaseModel):
    target_cefr: str


def cefr_to_min_band(cefr: str) -> float:
    return {"C2": 8.5, "C1": 7.0, "B2": 5.5, "B1": 4.0, "A2": 3.0, "A1": 0.0}.get(cefr, 7.0)


@router.post("/{exam_id}/upgrade")
async def upgrade_writing(
    exam_id: str,
    body: UpgradeRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
    provider: WritingEvaluator = Depends(get_ai_provider),
):
    is_premium = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)
    if not is_premium:
        raise HTTPException(status_code=402, detail="Essay upgrade is a Pro feature")

    ev = db.query(Evaluation).filter(Evaluation.exam_id == exam_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.user_id == user_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # Return cached result if already upgraded
    if ev.upgraded_text:
        return {"upgraded_text": ev.upgraded_text, "cached": True}

    target_cefr = body.target_cefr.upper()
    if target_cefr not in ("C2", "C1", "B2", "B1"):
        raise HTTPException(status_code=400, detail="Invalid target CEFR level")
    if not ev.overall_band:
        raise HTTPException(status_code=400, detail="No band score available for upgrade")

    current_band = ev.overall_band
    target_band = cefr_to_min_band(target_cefr)
    if target_band <= current_band:
        raise HTTPException(status_code=400, detail="Target level must be higher than current level")

    def _band_to_cefr(band: float) -> str:
        if band >= 8.5: return "C2"
        if band >= 7.0: return "C1"
        if band >= 5.5: return "B2"
        if band >= 4.0: return "B1"
        if band >= 3.0: return "A2"
        return "A1"

    current_cefr = _band_to_cefr(current_band)

    from app.services.prompts.writing import WRITING_UPGRADE
    prompt = (WRITING_UPGRADE
              .replace("__CURRENT_CEFR__", current_cefr)
              .replace("__CURRENT_BAND__", str(current_band))
              .replace("__TARGET_CEFR__", target_cefr)
              .replace("__TARGET_BAND__", str(target_band)))

    try:
        response = await provider._call_ai(prompt, ev.user_submission, max_tokens=4096, temperature=0.4)
        result = provider._parse_response(response)
    except Exception as e:
        logger.exception("Upgrade failed for exam=%s user=%s", exam_id, user_id)
        raise HTTPException(status_code=500, detail="Upgrade failed. Please try again.")

    ev.upgraded_text = result.get("upgraded_text", "")
    db.commit()

    return {
        "upgraded_text": ev.upgraded_text,
        "changes_summary": result.get("changes_summary", ""),
        "key_vocabulary": result.get("key_vocabulary", []),
        "cached": False,
    }
