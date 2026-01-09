from pathlib import Path
from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain_google_genai import ChatGoogleGenerativeAI

from app.tools.hello_tools import hello_cloud_tool
from app.tools.project_analyzer import project_analyzer
from app.tools.dockerfile_generator import dockerfile_generator
from app.tools.cloudrun_reviewer import cloudrun_review_report
from app.tools.cloudrun_config_generator import cloudrun_config_generator_tool

from app.runtime import get_run_env
env = get_run_env()

load_dotenv()


def load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "system.txt"
    return prompt_path.read_text(encoding="utf-8")

def create_agent_graph():
    system_prompt_env = f"""
        You are running in **{env}** mode.

        Rules:
        - If running on cloudrun:
            - DO NOT attempt to read local repositories
            - DO NOT write files or folders
            - ALWAYS output generated configs as markdown or structured JSON
        - If running locally:
            - You may read repo files
            - You may write generated files to disk
        """
    system_prompt = system_prompt_env + load_system_prompt()
    
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
    )

    tools = [hello_cloud_tool, project_analyzer, dockerfile_generator, cloudrun_review_report, cloudrun_config_generator_tool]

    # LangChain v1 agent (graph)
    graph = create_agent(
        model=llm,                 # can be a BaseChatModel instance :contentReference[oaicite:1]{index=1}
        tools=tools,
        system_prompt=system_prompt,
    )
    return graph
