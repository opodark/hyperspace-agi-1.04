# SPDX-License-Identifier: Apache-2.0
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import time

app = FastAPI()

class Node(BaseModel):
    node_id: str
    status: str = "active"
    last_seen: float
    hostname: str
    ram_gb: float
    model: str
    capabilities: list

class NodeRegister(BaseModel):
    node_id: str
    hostname: str
    ram_gb: float
    model: str
    capabilities: list

class Heartbeat(BaseModel):
    node_id: str

nodes = {}

@app.post("/register")
async def register_node(node: NodeRegister):
    if node.node_id in nodes:
        raise HTTPException(status_code=409, detail="Node already registered")
    nodes[node.node_id] = Node(node_id=node.node_id, last_seen=time.time(), **node.dict())
    return {"message": "Node registered successfully"}

@app.post("/heartbeat")
async def heartbeat(heartbeat: Heartbeat):
    if heartbeat.node_id not in nodes:
        raise HTTPException(status_code=404, detail="Node not found")
    nodes[heartbeat.node_id].last_seen = time.time()
    return {"message": "Heartbeat received"}

@app.get("/nodes")
async def get_nodes():
    return {node_id: node.dict() for node_id, node in nodes.items()}
