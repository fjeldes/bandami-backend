from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
import os
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
from app.core.limiter import limiter
from app.services.providers.base import SpeakingEvaluator
from app.core.config import get_settings
from datetime import datetime, timezone
import traceback
import logging

logger = logging.getLogger("ielts.speaking")
router = APIRouter()


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
    if exam.status != "pending":
        raise HTTPException(status_code=400, detail="Exam already processed")

    is_free = plan_info.get("tier", "free") != "premium"
    delay_hours = plan_info.get("feedback_delay_hours", 0)
    unlocks_at = compute_feedback_unlocks_at(delay_hours)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    exam.status = "processing"
    exam.eval_source = plan_info.get("eval_source", "daily")
    db.commit()

    try:
        audio_bytes = await audio.read()
        
        # Validate file size (max 25MB)
        if len(audio_bytes) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Audio file too large. Maximum size is 25MB.")
        
        # Validate MIME type
        allowed_mime = {"audio/webm", "audio/mpeg", "audio/mp4", "audio/wav", "audio/ogg"}
        if audio.content_type and audio.content_type not in allowed_mime:
            raise HTTPException(status_code=415, detail=f"Unsupported audio format: {audio.content_type}. Use WebM, MP3, MP4, WAV, or OGG.")
        
        transcription = await provider.transcribe_audio(audio_bytes, audio.filename or "audio.webm")
        result = await provider.evaluate_speaking(transcription, detailed=not is_free)

        # Save audio for playback
        audio_dir = "/app/static/audio"
        os.makedirs(audio_dir, exist_ok=True)
        audio_filename = f"{exam_id}.webm"
        with open(os.path.join(audio_dir, audio_filename), "wb") as f:
            f.write(audio_bytes)

        ev = Evaluation(
            exam_id=exam.id,
            user_submission=transcription,
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

        return EvaluationResponse(
            id=str(ev.id),
            exam_id=str(ev.exam_id),
            user_submission=transcription,
            overall_band=ev.overall_band,
            criteria_scores=ev.criteria_scores if is_visible else {},
            general_feedback=result.general_feedback or "",
            detailed_feedback=result.detailed_feedback if is_visible else None,
            grammar_corrections=result.grammar_corrections if is_visible else [],
            provider_used=provider.provider_name,
            ai_model_used=result.model,
            tokens_used=result.tokens,
            processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at,
            is_feedback_visible=is_visible,
            created_at=ev.created_at,
        )

    except Exception as e:
        settings = get_settings()
        tb = traceback.format_exc()
        if settings.debug:
            logger.error(f"Evaluation failed:\n{tb}")
        exam.status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail="Evaluation failed. Please try again.")


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
        raise HTTPException(status_code=404, detail="Evaluation not found")

    unlocks_at = ev.feedback_unlocks_at

    now = datetime.now(timezone.utc)
    if unlocks_at and unlocks_at.tzinfo is None:
        unlocks_at = unlocks_at.replace(tzinfo=timezone.utc)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    return EvaluationResponse(
        id=str(ev.id),
        exam_id=str(ev.exam_id),
        user_submission=ev.user_submission,
        overall_band=ev.overall_band,
        criteria_scores=ev.criteria_scores if is_visible else {},
        general_feedback=ev.general_feedback or "",
        detailed_feedback=ev.detailed_feedback if is_visible else None,
        grammar_corrections=ev.grammar_corrections if is_visible else [],
        provider_used=ev.provider_used,
        ai_model_used=ev.ai_model_used,
        tokens_used=ev.tokens_used,
        processing_time_ms=ev.processing_time_ms,
        feedback_unlocks_at=unlocks_at or now,
        is_feedback_visible=is_visible,
        created_at=ev.created_at,
    )


@router.get("/{exam_id}/audio")
async def get_speaking_audio(
    exam_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    """Stream the original audio recording (premium only)."""
    is_premium = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)
    if not is_premium:
        raise HTTPException(status_code=402, detail="Audio playback is a Premium feature")

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.user_id == user_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    audio_path = f"/app/static/audio/{exam_id}.webm"
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Audio not found")

    return FileResponse(audio_path, media_type="audio/webm")
