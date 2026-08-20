"""
wan2gp_server - REST API server for WanGP media generation.

Exposes WanGP's in-process Python API (shared/api.py) as a clean FastAPI
service with durable end-to-end episode projects plus scene-oriented generation
endpoints:

    POST /v1/projects
    GET  /v1/projects/{project_id}

    POST /v1/generations/text-to-image
    POST /v1/generations/text-to-video
    POST /v1/generations/image-to-video
    POST /v1/generations/video-to-video
    POST /v1/concatenations

Jobs are queued and processed one at a time (the GPU runs a single
generation at once); clients poll GET /v1/jobs/{job_id} for progress and
download outputs from GET /v1/jobs/{job_id}/files/{index}.

This integration uses WanGP by DeepBeepMeep. Use of the WanGP API is
subject to the WanGP Terms and Conditions.
"""

__version__ = "0.3.0"


def create_app(*args, **kwargs):
    """Import FastAPI lazily so project/pipeline helpers remain lightweight."""
    from .app import create_app as implementation

    return implementation(*args, **kwargs)
