"""Configured provider status; this endpoint does not probe external services."""

from fastapi import APIRouter

from .dependencies import PipelineDependency

router = APIRouter()


@router.get("/status")
async def get_status(pipeline: PipelineDependency):
    # Report the configured adapter modes.  Presence of an adapter does not
    # prove that its external endpoint or credentials are reachable.
    search_mode = getattr(pipeline.retriever, "provider", "mock")
    judge_mode = getattr(pipeline.evidence_judge, "provider", "off") if pipeline.evidence_judge else "off"
    search_ready = search_mode == "tavily"
    judge_ready = judge_mode == "mimo"
    live = search_ready
    messages = {
        ("mock", "off"): "当前为离线演示，未启用联网搜索或模型判断",
        ("tavily", "off"): "已启用联网搜索，尚未启用模型判断",
        ("mock", "mimo"): "已启用模型判断，当前未启用联网搜索",
        ("tavily", "mimo"): "已启用联网搜索与模型判断；外部连通性尚未验证",
    }
    message = messages.get((search_mode, judge_mode), "当前配置尚未识别")
    segment_mode = "mimo" if getattr(pipeline, "segmenter", None) else "rules"
    message += "；语句切分：" + ("模型语义切分（失败时回退本地规则）" if segment_mode == "mimo" else "本地规则")
    return {"search_mode": search_mode, "judge_mode": judge_mode,
            "segment_mode": segment_mode,
            "ready": bool(search_ready and judge_ready), "live": live, "message": message}


