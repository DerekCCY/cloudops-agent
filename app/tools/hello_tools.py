from langchain_core.tools import tool

@tool
def hello_cloud_tool(project: str) -> str:
    """Say hello to a cloud project."""
    return f"CloudOps Agent received project: {project}"
