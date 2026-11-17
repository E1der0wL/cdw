"""The local server's exposed operations, shared by both ends of the boundary."""


class RemoteApi:
    SERVICE_METHODS = frozenset(("save_preset", "list_presets", "get_preset", "load_appearance", "save_appearance",
                                 "list_runs", "last_success", "workspace_tables", "list_tables", "release_result"))
    TASK_METHODS = frozenset(("list_tasks", "get_task", "save_task", "delete_task"))
    SCHEDULE_METHODS = frozenset(("list_workflows", "get_workflow", "save_workflow", "delete_workflow",
                                  "list_schedules", "get_schedule", "save_schedule", "save_bundle",
                                  "set_schedule_enabled", "delete_schedule", "list_runs", "get_run"))
    JOB_OPERATIONS = frozenset(("collect", "preview", "query_workspace", "query", "copy_result", "export_result",
                                "save_result_table", "run_task"))
    QUERY_OPERATIONS = frozenset(("query", "query_workspace"))
