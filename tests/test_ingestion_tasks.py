from app.workers.celery_app import celery_app


def test_worker_exports_only_active_pipeline_task():
    task_names = set(celery_app.tasks.keys())
    pipeline_task = celery_app.tasks["grasp.run_pipeline"]

    assert "grasp.run_pipeline" in task_names
    assert pipeline_task.name == "grasp.run_pipeline"
    assert pipeline_task.run.__name__ == "run_grasp_pipeline"
    assert "grasp.ingest_cookbook" not in task_names
    assert "grasp.delete_cookbook_vectors" not in task_names
