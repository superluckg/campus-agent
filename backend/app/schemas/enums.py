"""系统内稳定使用的枚举。"""

from enum import Enum


class IntentName(str, Enum):
    CAREER_RESUME = "career_resume"
    CAREER_RECRUITMENT = "career_recruitment"
    CAMPUS_NETWORK = "campus_network"
    CAMPUS_AFFAIRS = "campus_affairs"
    LEARNING_DOCUMENT = "learning_document"
    LEARNING_PLAN = "learning_plan"
    LEARNING_GPA = "learning_gpa"
    COMPOUND = "compound"
    UNCLEAR = "unclear"
    SMALL_TALK = "small_talk"
    OUT_OF_SCOPE = "out_of_scope"


class AgentName(str, Enum):
    CAREER = "CareerAgent"
    CAMPUS = "CampusAgent"
    LEARNING = "LearningAgent"


class ToolName(str, Enum):
    AGENTIC_RETRIEVAL = "agentic_retrieval"
    FIXED_WEB_SEARCH = "fixed_web_search"
    DOCUMENT_READER = "document_reader"
    GPA_CALCULATOR = "gpa_calculator"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QueryStrategy(str, Enum):
    ORIGINAL = "original"
    REWRITE = "rewrite"
    HYDE = "hyde"
    MULTI_QUERY = "multi_query"


class RetrievalSource(str, Enum):
    LOCAL_KNOWLEDGE = "local_knowledge"
    FIXED_WEB_SOURCES = "fixed_web_sources"
    MEMORY = "memory"

