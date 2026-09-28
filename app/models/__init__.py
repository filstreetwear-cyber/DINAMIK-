"""
Import every model module here so `Base.metadata` is fully populated
before Alembic's `--autogenerate` (or anything else) inspects it. A model
that exists but isn't imported anywhere is invisible to the metadata and
silently missing from migrations — this file exists specifically to
prevent that.
"""
from app.models.agent_run import AgentEvent, AgentRun, ErrorLog, ToolCall
from app.models.ai_claim import AIClaim
from app.models.ai_recommendation import AIRecommendation
from app.models.amazon import AmazonListing, AmazonProduct, AmazonSeller
from app.models.auditor_package import AuditorPackage
from app.models.base import Base
from app.models.browser import BrowserSession, BrowserTask
from app.models.company import Brand, Company
from app.models.contact import Contact, ProspectStatus
from app.models.evidence import Evidence
from app.models.market import Competitor, Keyword
from app.models.marketplace import Category, Marketplace
from app.models.opportunity import Opportunity, Score
from app.models.research import ResearchJob, ResearchResult
from app.models.website import WebsiteProfile
from app.models.weekly_scan import EntitySnapshot, WeeklyScan

__all__ = [
    "AIClaim",
    "AIRecommendation",
    "AgentEvent",
    "AgentRun",
    "AmazonListing",
    "AmazonProduct",
    "AmazonSeller",
    "AuditorPackage",
    "Base",
    "Brand",
    "BrowserSession",
    "BrowserTask",
    "Category",
    "Company",
    "Competitor",
    "Contact",
    "EntitySnapshot",
    "ErrorLog",
    "Evidence",
    "Keyword",
    "Marketplace",
    "Opportunity",
    "ProspectStatus",
    "ResearchJob",
    "ResearchResult",
    "Score",
    "ToolCall",
    "WebsiteProfile",
    "WeeklyScan",
]
