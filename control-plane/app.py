# SPDX-License-Identifier: Apache-2.0
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests

app = FastAPI()
tasks = {}


class TaskCreate(BaseModel):
    task_id: str
    payload: dict


class TaskAssign(BaseModel):
    task_id: str


class TaskComplete(BaseModel):
    task_id: str


@app.post("/task/create")
async def create_task(task: TaskCreate):
    if task.task_id in tasks:
        raise HTTPException(status_code=400, detail="Task already exists")
    tasks[task.task_id] = {
        "status": "created",
        "assigned_node": None,
        "payload": task.payload,
    }
    return {"task_id": task.task_id}


@app.post("/task/assign")
async def assign_task(task: TaskAssign):
    if task.task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    response = requests.get("http://authority:8080/nodes")
    nodes = response.json()
    active_nodes = [node for node in nodes if node["status"] == "active"]
    if not active_nodes:
        raise HTTPException(status_code=404, detail="No active nodes available")
    assigned_node = active_nodes[0]["node_id"]
    tasks[task.task_id]["status"] = "assigned"
    tasks[task.task_id]["assigned_node"] = assigned_node
    return {"task_id": task.task_id, "assigned_node": assigned_node}


@app.post("/task/complete")
async def complete_task(task: TaskComplete):
    if task.task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    tasks[task.task_id]["status"] = "completed"
    return {"task_id": task.task_id}


@app.get("/tasks")
async def get_tasks():
    return tasks
