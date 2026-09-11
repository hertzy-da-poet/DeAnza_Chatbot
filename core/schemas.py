"""Data Schemas for DA RAG chatbot"""
from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field

#We Chunk the Meta Data to enable direct database queries without having to run the expensive vector maths (e.g. WHERE metadata->>'code'='CIS 22A')
class ChunkMetaData(BaseModel):
    source_type: str = Field(..., description = "'course', 'page', or 'section'")
    code: Optional[str] = Field(None, description="Course code e.g. 'CIS 22A'")
    title: Optional[str] = Field(None, description="Title of course or page")
    units: Optional[float] = Field(None, description="Course unit value")
    prereqs: Optional[str] = Field(None, description="Course prerequisite text")
    crn: Optional[str] = Field(None, description="Class CRN number")
    quarter: Optional[str] = Field(None, description="Academic quarter e.g. 'fall-2026")
    extra: Dict[str, Any] = Field(default_factory=dict)

# Stores the input data being split into Chunks
class Chunk(BaseModel):
    source_type: str
    source_url: str
    doc_id: str
    title: str
    chunk_text: str
    embedding: Optional[List[float]] = None
    metadata: ChunkMetaData

# This class is to standardize the search output, calculate relevant score, and optimize searching by extracting only what the LLM needs
class SearchResult(BaseModel):
    id: int
    text: str
    meta: Dict[str, Any]
    url: Optional[str]
    score: float = 0.0

# facilitate the chatting task for the LLM
class ChatMessage (BaseModel):
    role: str #"user", "assistant","model",...
    content: str

# faciliate intaking information from users
class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = Field (default_factory=list)



#THis class is to define each single chat message
class MessageItem(BaseModel):
    role: str
    content: str

#This class is to complete the JSON's payload from the user to backend
class ChatRequest(BaseModel): 
    message: str
    history: Optional[List[MessageItem]] = []