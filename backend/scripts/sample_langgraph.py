"""Beginner LangGraph intent router with human-in-the-loop Supabase CRUD."""
import os
from datetime import date
from typing import Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError
#from supabase import create_client
from typing_extensions import TypedDict

load_dotenv()
#db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

class Intent(BaseModel):
    action: Literal["read", "create", "update"] = Field(description="Requested database operation")
    record_id: int | None = Field(default=None, description="Primary key, if explicitly given")
    full_name: str | None = Field(default=None, description="Full name, if given")
    dob: date | None = Field(default=None, description="Date of birth, if given")

class State(TypedDict, total=False):
    message: str
    intent: dict
    result: str

def router(state: State) -> dict:
    prompt = (
        "Extract the user's database intention and explicitly provided values. "
        "For update, full_name and dob are optional and mean fields to change. "
        "Never invent IDs or values. Date format is YYYY-MM-DD. "
        "Only the read, create, and update operations are supported.\n"
        f"User: {state['message']}"
    )
    parsed = llm.with_structured_output(Intent).invoke(prompt)
    print(f"Parsed is {parsed}")
    return {"intent": parsed.model_dump(mode="json")}

def choose_action(state: State) -> str:
    print(f"Choosing action from {state['intent']}")
    return state["intent"]["action"]

def ask(field: str, question: str) -> str:
    """Pause the graph and wait for the next user reply."""
    answer = interrupt(question)
    print(f"User replied to {field}: {answer}")
    return str(answer).strip()

def get_id(data: dict) -> int:
    while data.get("record_id") is None:
        raw = ask("record_id", "What is the record ID (a number)?")
        try:
            value = int(raw)
            if value < 1:
                raise ValueError()
            data["record_id"] = value
        except ValueError:
            print("Please enter a positive integer ID.")
    return int(data["record_id"])

def get_name(data: dict) -> str:
    while not data.get("full_name"):
        data["full_name"] = ask("full_name", "What is the full name?")
    return str(data["full_name"]).strip()

def get_dob(data: dict) -> str:
    while not data.get("dob"):
        raw = ask("dob", "What is the date of birth (YYYY-MM-DD)?")
        try:
            data["dob"] = date.fromisoformat(raw).isoformat()
        except ValueError:
            print("Invalid date. Please use YYYY-MM-DD.")
    return date.fromisoformat(str(data["dob"])).isoformat()

def read_node(state: State) -> dict:
    data = dict(state["intent"])
    record_id = get_id(data)
    rows = "Record Found" 
    return {"result": str(rows[0]) if rows else "No record found for that ID."}

def create_node(state: State) -> dict:
    data = dict(state["intent"])
    name = get_name(data)
    dob = get_dob(data)
    rows = "Record Created"
    return {"result": f"Created: {rows}"}

def update_node(state: State) -> dict:
    data = dict(state["intent"])
    record_id = get_id(data)
    if not data.get("full_name") and not data.get("dob"):
        # Ask for multiple values in a simple, structured format.
        while True:
            raw = ask("changes", "What should I update? Enter name=New Name; dob=YYYY-MM-DD (one or both).")
            try:
                changes = {}
                for item in raw.split(";"):
                    key, value = [part.strip() for part in item.split("=", 1)]
                    if key == "name":
                        changes["full_name"] = value
                    elif key == "dob":
                        changes["dob"] = date.fromisoformat(value).isoformat()
                    else:
                        raise ValueError("Only name and dob are supported")
                if not changes or ("full_name" in changes and not changes["full_name"]):
                    raise ValueError("At least one nonempty value is required")
                data.update(changes)
                break
            except (ValueError, TypeError) as exc:
                print(f"Invalid update: {exc}")
    changes = {}
    if data.get("full_name"):
        changes["full_name"] = str(data["full_name"]).strip()
    if data.get("dob"):
        changes["dob"] = date.fromisoformat(str(data["dob"])).isoformat()
    rows = db.table("profiles").update(changes).eq("id", record_id).execute().data
    return {"result": f"Updated: {rows}" if rows else "No record found for that ID."}

builder = StateGraph(State)
builder.add_node("router", router)
builder.add_node("read", read_node)
builder.add_node("create", create_node)
builder.add_node("update", update_node)
builder.add_edge(START, "router")
builder.add_conditional_edges("router", choose_action, {"read": "read", "create": "create", "update": "update"})
for node in ("read", "create", "update"):
    builder.add_edge(node, END)
graph = builder.compile(checkpointer=InMemorySaver())

if __name__ == "__main__":
    print("Supabase assistant (read/create/update). Type quit to exit.")
    while True:
        message = input("\nYou: ").strip()
        if message.lower() in {"quit", "exit"}:
            break
        if not message:
            continue
        config = {"configurable": {"thread_id": "local-demo"}}
        payload = {"message": message}
        try:
            while True:
                output = graph.invoke(payload, config=config)
                if "__interrupt__" not in output:
                    print("Assistant:", output.get("result", "No result"))
                    break
                question = output["__interrupt__"][0].value
                reply = input(f"Assistant: {question}\nYou: ")
                payload = Command(resume=reply)
        except (ValidationError, Exception) as exc:
            print(f"Request failed: {exc}")
