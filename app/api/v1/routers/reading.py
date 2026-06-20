import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone

from app.db.deps import get_db
from app.models.exam import Exam, Evaluation
from app.schemas.evaluation import EvaluationResponse, ExamCreate, ExamResponse
from app.core.auth import get_current_user, get_user_plan_info, check_daily_limit, get_ai_provider, compute_feedback_unlocks_at
from app.services.providers.base import ReadingEvaluator, ProviderUnavailableError

logger = logging.getLogger("ielts.reading")
router = APIRouter()


class ReadingSubmission(BaseModel):
    exam_id: str
    answers: dict = Field(default_factory=dict)


@router.post("/exam", response_model=ExamResponse)
async def create_reading_exam(
    body: ExamCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    exam = Exam(
        user_id=user_id,
        question_id=str(body.question_id) if body.question_id else None,
        exam_type="reading",
        status="pending",
        attempt_number=body.attempt_number,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return ExamResponse(
        id=str(exam.id), user_id=str(exam.user_id),
        question_id=str(exam.question_id) if exam.question_id else None,
        exam_type=exam.exam_type, task_type=exam.task_type,
        status=exam.status, attempt_number=exam.attempt_number,
        eval_source=exam.eval_source, created_at=exam.created_at,
    )


@router.post("/", response_model=EvaluationResponse)
async def evaluate_reading(
    submission: ReadingSubmission,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(check_daily_limit),
    provider: ReadingEvaluator = Depends(get_ai_provider),
) -> EvaluationResponse:
    exam = db.query(Exam).filter(Exam.id == submission.exam_id, Exam.user_id == user_id).first()
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
        result = await provider.evaluate_reading(submission.answers, detailed=not is_free)

        ev = Evaluation(
            exam_id=exam.id,
            user_submission=str(submission.answers),
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

        return EvaluationResponse(
            id=str(ev.id), exam_id=str(ev.exam_id), user_submission=str(submission.answers),
            overall_band=ev.overall_band, criteria_scores=ev.criteria_scores if is_visible else {},
            general_feedback=result.general_feedback or "", detailed_feedback=result.detailed_feedback if is_visible else None,
            grammar_corrections=[], provider_used=provider.provider_name,
            ai_model_used=result.model, tokens_used=result.tokens, processing_time_ms=result.processing_time_ms,
            feedback_unlocks_at=unlocks_at, is_feedback_visible=is_visible, created_at=ev.created_at,
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
async def get_reading_evaluation(
    exam_id: str, user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db), plan_info: dict = Depends(get_user_plan_info),
):
    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.user_id == user_id).first()
    if not exam: raise HTTPException(status_code=404, detail="Exam not found")
    ev = db.query(Evaluation).filter(Evaluation.exam_id == exam_id).first()
    if not ev: raise HTTPException(status_code=404, detail="Evaluation not found")

    unlocks_at = ev.feedback_unlocks_at
    now = datetime.now(timezone.utc)
    if unlocks_at and unlocks_at.tzinfo is None: unlocks_at = unlocks_at.replace(tzinfo=timezone.utc)
    is_visible = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)

    return EvaluationResponse(
        id=str(ev.id), exam_id=str(ev.exam_id), user_submission=ev.user_submission,
        overall_band=ev.overall_band, criteria_scores=ev.criteria_scores if is_visible else {},
        general_feedback=ev.general_feedback or "", detailed_feedback=ev.detailed_feedback if is_visible else None,
        grammar_corrections=ev.grammar_corrections if is_visible else [], provider_used=ev.provider_used,
        ai_model_used=ev.ai_model_used, tokens_used=ev.tokens_used, processing_time_ms=ev.processing_time_ms,
        feedback_unlocks_at=unlocks_at or now, is_feedback_visible=is_visible, created_at=ev.created_at,
    )
