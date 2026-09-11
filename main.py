import json
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from core.schemas import ChatRequest
from core.chat import stream_chat
from core.db import get_db
from core.rate_limiter import chat_limiter, get_client_ip

#Initialize the api
app = FastAPI(title="De Anza AI Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dachatbot.com"
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    with get_db() as conn: 
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    return {
        "status": "ok",
        "db": "connected",
    }

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, request: Request):

    # Check rate limit per device ID (fallback to IP if header missing)
    device_id = request.headers.get("x-device-id")
    client_key = device_id.strip() if device_id and device_id.strip() else get_client_ip(request)
    allowed, retry_after = chat_limiter.check(client_key)

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You're asking questions a bit too fast! Please wait {retry_after} seconds before sending your next message."
        )

    #Streaming generator
    async def event_generator():
        async for item in stream_chat(
            req.message,
            [h.model_dump() for h in req.history]
        ):
            if isinstance(item, dict):
                yield f"data: {json.dumps(item)}\n\n"
            else:
                yield f"data: {json.dumps({'text': item})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )

app.mount("/", StaticFiles(
    directory="public",
    html=True,
), name="public")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host="127.0.0.1", 
        port=8000, 
        reload=True,
    )
