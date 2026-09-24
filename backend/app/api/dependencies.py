"""Resolve application-owned services without module-level database connections."""

from typing import Annotated

from fastapi import Depends, Request

from ..pipeline import Pipeline


def get_pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


PipelineDependency = Annotated[Pipeline, Depends(get_pipeline)]
