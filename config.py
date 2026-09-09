import os
from dotenv import load_dotenv

load_dotenv()

# Gemini configuration (deprecated in v2 in favor of OpenRouter)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")

# Recommended models for selection and comparison
OPENROUTER_RECOMMENDED_MODELS = [
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash (Fast & cheap)"),
    ("meta-llama/llama-3-8b-instruct", "Llama 3 8B Instruct (Very low cost)"),
    ("anthropic/claude-3.5-haiku", "Claude 3.5 Haiku (Fast & high quality)"),
    ("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet (Premium reasoning)"),
    ("openai/gpt-4o-mini", "GPT-4o Mini (Balanced premium)"),
]

# Email SMTP settings
SMTP_SENDER_EMAIL = os.getenv("SMTP_SENDER_EMAIL")
SMTP_SENDER_PASSWORD = os.getenv("SMTP_SENDER_PASSWORD")
DEFAULT_SMTP_RECEIVER_EMAILS = ["recipient@example.com"]
SMTP_RECEIVER_EMAIL = os.getenv("SMTP_RECEIVER_EMAIL", ", ".join(DEFAULT_SMTP_RECEIVER_EMAILS))

# Coverage configuration
COVERAGE_WINDOW_HOURS = 48
RUN_TIME_CANBERRA = "03:00"
TIMEZONE_CANBERRA = "Australia/Sydney"

# Runtime tuning
MAX_DDG_RESULTS_PER_QUERY = int(os.getenv("MAX_DDG_RESULTS_PER_QUERY", "8"))
DDG_SEARCH_DELAY_SECONDS = float(os.getenv("DDG_SEARCH_DELAY_SECONDS", "1.0"))
SOURCE_DISCOVERY_DELAY_SECONDS = float(os.getenv("SOURCE_DISCOVERY_DELAY_SECONDS", "0.5"))
ENABLE_DDG_FALLBACK = os.getenv("ENABLE_DDG_FALLBACK", "false").strip().lower() in ("1", "true", "yes", "on")
MAX_NATIVE_CANDIDATES_PER_SOURCE = int(os.getenv("MAX_NATIVE_CANDIDATES_PER_SOURCE", "30"))
MAX_REVIEW_CANDIDATES_PER_SOURCE = int(os.getenv("MAX_REVIEW_CANDIDATES_PER_SOURCE", "25"))
DATE_POOR_SOURCE_THRESHOLD = float(os.getenv("DATE_POOR_SOURCE_THRESHOLD", "0.8"))
DATE_POOR_SOURCE_SAMPLE_PER_RUN = int(os.getenv("DATE_POOR_SOURCE_SAMPLE_PER_RUN", "3"))
DATE_POOR_SAMPLE_MIN_TEXT_CHARS = int(os.getenv("DATE_POOR_SAMPLE_MIN_TEXT_CHARS", "300"))
FULL_TEXT_CHAR_LIMIT = int(os.getenv("FULL_TEXT_CHAR_LIMIT", "12000"))
LLM_ITEM_TEXT_CHAR_LIMIT = int(os.getenv("LLM_ITEM_TEXT_CHAR_LIMIT", "12000"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "3"))
ENABLE_ENRICHMENT_CACHE = os.getenv("ENABLE_ENRICHMENT_CACHE", "true").strip().lower() in ("1", "true", "yes", "on")
ENRICHMENT_CACHE_DIR = os.getenv("ENRICHMENT_CACHE_DIR", os.path.join(".cache", "enrichment"))
ENRICHMENT_CACHE_MAX_AGE_HOURS = int(os.getenv("ENRICHMENT_CACHE_MAX_AGE_HOURS", "168"))
PDF_TEXT_MAX_PAGES = int(os.getenv("PDF_TEXT_MAX_PAGES", "25"))
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_LEDGER_PATH = os.path.abspath(os.path.join(PROJECT_DIR, os.getenv("SEEN_LEDGER_PATH", os.path.join(".scanner_state", "seen_items.json"))))

CHECKPOINT_OVERLAP_HOURS = 6
PENDING_RETRIES_PER_SOURCE = 5
PENDING_MAX_ATTEMPTS = 6
MAX_CACHED_DECISIONS = 5000
EVIDENCE_MIN_CHARS = 300
TEXT_STORAGE_CHAR_LIMIT = 60000

# Credible official think tanks.
THINK_TANKS = [
    "Center for Strategic and International Studies (CSIS)",
    "Council on Foreign Relations (CFR)",
    "Atlantic Council",
    "Carnegie Endowment for International Peace",
    "Woodrow Wilson International Center for Scholars",
    "International Institute for Strategic Studies (IISS)",
    "Royal United Services Institute (RUSI)",
    "Chatham House",
    "RAND Corporation",
    "Lowy Institute",
    "Australian Strategic Policy Institute (ASPI)",
    "Stockholm International Peace Research Institute (SIPRI)",
    "Observer Research Foundation (ORF)",
    "Center for a New American Security (CNAS)",
    "European Council on Foreign Relations (ECFR)",
    "Hoover Institution",
    "Hudson Institute",
    "Foreign Policy Research Institute (FPRI)",
    "Institute for the Study of War (ISW)",
    "War on the Rocks",
    "National Bureau of Economic Research (NBER)",
]

# Source registry used by v3 discovery and audit logic. RSS-capable sources keep
# their feed URLs; search-only sources get source-specific DuckDuckGo queries.
DISCOVERY_SOURCES = [
    {
        "name": "Center for Strategic and International Studies (CSIS)",
        "domain": "csis.org",
        # CSIS's RSS endpoint is stale: as of July 2026 it returns 2016 items.
        # The homepage exposes current analysis/event links and is discoverable.
        "base_url": "https://www.csis.org/",
        "index_paths": ["/"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Council on Foreign Relations (CFR)",
        "domain": "cfr.org",
        "rss_url": "https://www.cfr.org/feed",
        "methods": ["rss", "page_extract"],
    },
    {
        "name": "Atlantic Council",
        "domain": "atlanticcouncil.org",
        "rss_url": "https://www.atlanticcouncil.org/feed/",
        "methods": ["rss", "page_extract"],
    },
    {
        "name": "Carnegie Endowment for International Peace",
        "domain": "carnegieendowment.org",
        "base_url": "https://carnegieendowment.org/",
        "index_paths": ["/research", "/en/research", "/collections/featured-research", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Woodrow Wilson International Center for Scholars",
        "domain": "wilsoncenter.org",
        "base_url": "https://www.wilsoncenter.org/",
        "index_paths": ["/publications", "/articles", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "International Institute for Strategic Studies (IISS)",
        "domain": "iiss.org",
        "base_url": "https://www.iiss.org/",
        "index_paths": ["/online-analysis/", "/research-papers/", "/events/"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Royal United Services Institute (RUSI)",
        "domain": "rusi.org",
        "base_url": "https://www.rusi.org/",
        "index_paths": ["/explore-our-research/publications", "/explore-our-research/commentary", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Chatham House",
        "domain": "chathamhouse.org",
        "base_url": "https://www.chathamhouse.org/",
        "index_paths": ["/publications/research-publications", "/publications", "/events", "/comment"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "RAND Corporation",
        "domain": "rand.org",
        "base_url": "https://www.rand.org/",
        "index_paths": ["/pubs", "/blog", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Lowy Institute",
        "domain": "lowyinstitute.org",
        "rss_url": "https://www.lowyinstitute.org/feed",
        "methods": ["rss", "page_extract"],
    },
    {
        "name": "Australian Strategic Policy Institute (ASPI)",
        "domain": "aspistrategist.org.au",
        "rss_url": "https://www.aspistrategist.org.au/feed/",
        "methods": ["rss", "page_extract"],
    },
    {
        "name": "Stockholm International Peace Research Institute (SIPRI)",
        "domain": "sipri.org",
        "base_url": "https://www.sipri.org/",
        "index_paths": ["/publications", "/commentary", "/media/press-release"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Observer Research Foundation (ORF)",
        "domain": "orfonline.org",
        "base_url": "https://www.orfonline.org/",
        "index_paths": ["/research", "/expert-speak", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Center for a New American Security (CNAS)",
        "domain": "cnas.org",
        "base_url": "https://www.cnas.org/",
        "index_paths": ["/publications", "/commentary", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "European Council on Foreign Relations (ECFR)",
        "domain": "ecfr.eu",
        "rss_url": "https://ecfr.eu/feed/",
        "methods": ["rss", "page_extract"],
    },
    {
        "name": "Hoover Institution",
        "domain": "hoover.org",
        "rss_url": "https://www.hoover.org/feed",
        "methods": ["rss", "page_extract"],
    },
    {
        "name": "Hudson Institute",
        "domain": "hudson.org",
        "base_url": "https://www.hudson.org/",
        "index_paths": ["/research", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Foreign Policy Research Institute (FPRI)",
        "domain": "fpri.org",
        "base_url": "https://www.fpri.org/",
        "index_paths": ["/articles", "/research", "/events"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "Institute for the Study of War (ISW)",
        "domain": "understandingwar.org",
        "base_url": "https://www.understandingwar.org/",
        "index_paths": ["/publications"],
        "methods": ["ddg", "page_extract"],
    },
    {
        "name": "War on the Rocks",
        "domain": "warontherocks.com",
        "rss_url": "https://warontherocks.com/feed/",
        "methods": ["rss", "page_extract"],
    },
    {
        "name": "National Bureau of Economic Research (NBER)",
        "domain": "nber.org",
        "rss_url": "https://www.nber.org/rss/new.xml",
        "base_url": "https://www.nber.org/",
        "index_paths": ["/papers", "/books-and-chapters", "/digest"],
        "methods": ["rss", "page_extract"],
    },
]

DISABLED_THINK_TANKS = {
    name.strip()
    for name in os.getenv("DISABLED_THINK_TANKS", "Observer Research Foundation (ORF)").split("|")
    if name.strip()
}

THINK_TANKS = [
    source
    for source in THINK_TANKS
    if source not in DISABLED_THINK_TANKS
]

DISCOVERY_SOURCES = [
    source
    for source in DISCOVERY_SOURCES
    if source["name"] not in DISABLED_THINK_TANKS
]

SOURCE_DOMAINS = {source["name"]: source["domain"] for source in DISCOVERY_SOURCES}

# Verified active RSS feeds.
RSS_FEEDS = {
    source["name"]: source["rss_url"]
    for source in DISCOVERY_SOURCES
    if source.get("rss_url")
}

# Sources that need search-first discovery.
SEARCH_ONLY_THINK_TANKS = [
    source["name"]
    for source in DISCOVERY_SOURCES
    if "ddg" in source.get("methods", []) and not source.get("rss_url")
]

# Recall-first search groups. Each search-only source is queried once for each
# group, which avoids weak multi-site batching while keeping volume predictable.
SEARCH_QUERY_GROUPS = [
    [
        "economic security",
        "critical minerals",
        "supply chain security",
        "critical dependencies",
        "resilience",
    ],
    [
        "sanctions",
        "export controls",
        "economic coercion",
        "FDI screening",
        "investment screening",
    ],
    [
        "artificial intelligence",
        "semiconductors",
        "emerging technology",
        "data governance",
        "cyber",
    ],
    [
        "energy security",
        "food security",
        "water security",
        "critical infrastructure",
        "maritime chokepoint",
    ],
    [
        "sovereign debt",
        "debt diplomacy",
        "currency weaponisation",
        "industrial policy",
        "friendshoring",
    ],
]

# Target topics for scanning.
ECONOMIC_SECURITY_TOPICS = [
    "Critical Minerals",
    "other Critical Dependencies",
    "Economic Coercion",
    "Export Controls and Sanctions",
    "Reshoring and Friendshoring",
    "Foreign Direct Investment (FDI) Screening",
    "Weaponisation of Currency",
    "Sovereign Debt and Debt-Trap Diplomacy",
    "Intellectual Property (IP) Theft",
    "Emerging Technologies",
    "Energy Independence and Transition",
    "Food and Water Security",
    "Critical Infrastructure Protection",
    "Economic Espionage",
]

# Topic ontology used before and during LLM review. The broad matcher uses
# keywords for recall; the LLM prompt uses inclusion/exclusion rules for
# precision.
TOPIC_ONTOLOGY = {
    "Critical Minerals": {
        "keywords": [
            "critical mineral",
            "rare earth",
            "lithium",
            "cobalt",
            "nickel",
            "graphite",
            "gallium",
            "germanium",
            "mineral processing",
            "battery minerals",
            "strategic minerals",
        ],
        "include": "Mining, processing, refining, stockpiling, trade controls, or supply security for minerals essential to defence, energy, or advanced technology.",
        "exclude": "General mining-sector commentary without strategic supply-chain or national-security implications.",
    },
    "other Critical Dependencies": {
        "keywords": [
            "critical dependency",
            "supply chain",
            "chokepoint",
            "strategic dependency",
            "industrial base",
            "resilience",
            "single point of failure",
            "supplier concentration",
        ],
        "include": "Non-mineral strategic dependencies, supply-chain chokepoints, concentration risks, or industrial-base vulnerabilities.",
        "exclude": "Routine trade or business coverage without dependency, resilience, or strategic leverage implications.",
    },
    "Economic Coercion": {
        "keywords": [
            "economic coercion",
            "coercive trade",
            "trade punishment",
            "boycott",
            "embargo",
            "geoeconomic pressure",
            "weaponized trade",
            "retaliatory tariffs",
        ],
        "include": "Use or threat of economic tools to compel state, firm, or population behaviour.",
        "exclude": "Normal market competition or tariffs without coercive strategic intent.",
    },
    "Export Controls and Sanctions": {
        "keywords": [
            "export control",
            "export controls",
            "sanction",
            "sanctions",
            "entity list",
            "trade control",
            "trade controls",
            "technology controls",
            "dual-use",
            "embargo",
            "restricted party",
        ],
        "include": "Export controls, sanctions, dual-use restrictions, enforcement, evasion, or allied coordination.",
        "exclude": "Generic export growth data without control, restriction, or sanctions relevance.",
    },
    "Reshoring and Friendshoring": {
        "keywords": [
            "reshoring",
            "friendshoring",
            "nearshoring",
            "onshoring",
            "supply-chain diversification",
            "trusted suppliers",
            "industrial policy",
        ],
        "include": "Industrial relocation, allied supply-chain diversification, trusted supply networks, or domestic production policy.",
        "exclude": "General manufacturing news without strategic resilience or diversification content.",
    },
    "Foreign Direct Investment (FDI) Screening": {
        "keywords": [
            "FDI screening",
            "investment screening",
            "CFIUS",
            "foreign investment",
            "national security review",
            "inbound investment",
            "outbound investment",
        ],
        "include": "Foreign investment screening, strategic-sector ownership, technology transfer risk, or national-security investment reviews.",
        "exclude": "Routine investment announcements without screening or strategic-control implications.",
    },
    "Weaponisation of Currency": {
        "keywords": [
            "currency weaponisation",
            "dollar weapon",
            "reserve currency",
            "payment system",
            "SWIFT",
            "de-dollarization",
            "financial sanctions",
        ],
        "include": "Use of currencies, payment systems, reserves, or financial infrastructure as instruments of state power.",
        "exclude": "Ordinary exchange-rate commentary without strategic finance implications.",
    },
    "Sovereign Debt and Debt-Trap Diplomacy": {
        "keywords": [
            "sovereign debt",
            "debt distress",
            "debt diplomacy",
            "debt-trap",
            "Belt and Road debt",
            "infrastructure lending",
            "debt restructuring",
        ],
        "include": "Sovereign lending, debt distress, strategic infrastructure finance, or creditor leverage over states.",
        "exclude": "General fiscal commentary without external leverage or strategic lending content.",
    },
    "Intellectual Property (IP) Theft": {
        "keywords": [
            "IP theft",
            "intellectual property",
            "trade secret",
            "technology transfer",
            "forced transfer",
            "copyright",
            "patent",
            "research security",
        ],
        "include": "Theft, coercive transfer, leakage, or protection of economically strategic intellectual property.",
        "exclude": "Routine copyright disputes unless tied to strategic technology or national economic capability.",
    },
    "Emerging Technologies": {
        "keywords": [
            "artificial intelligence",
            "AI",
            "semiconductor",
            "quantum",
            "biotechnology",
            "advanced computing",
            "chips",
            "frontier model",
            "data center",
            "autonomous systems",
            "emerging technology",
        ],
        "include": "Strategic technology competition, dual-use technologies, compute, AI governance, semiconductor supply, biotech, quantum, or allied tech policy.",
        "exclude": "Consumer technology coverage without strategic, dual-use, supply-chain, or governance implications.",
    },
    "Energy Independence and Transition": {
        "keywords": [
            "energy security",
            "energy independence",
            "energy transition",
            "LNG",
            "oil",
            "gas",
            "grid",
            "renewables",
            "electricity",
            "nuclear energy",
        ],
        "include": "Energy supply security, transition dependencies, energy leverage, grid resilience, or strategic fuel markets.",
        "exclude": "Pure climate advocacy without supply, resilience, industry, or security implications.",
    },
    "Food and Water Security": {
        "keywords": [
            "food security",
            "water security",
            "agriculture",
            "grain",
            "fertilizer",
            "drought",
            "water infrastructure",
            "fisheries",
            "crop",
            "supply shock",
        ],
        "include": "Food, water, agricultural, or fertilizer vulnerabilities affecting stability, resilience, or strategic leverage.",
        "exclude": "Local public-health or environment stories without economic-security implications.",
    },
    "Critical Infrastructure Protection": {
        "keywords": [
            "critical infrastructure",
            "cybersecurity",
            "port",
            "pipeline",
            "grid",
            "telecommunications",
            "undersea cable",
            "satellite",
            "maritime chokepoint",
            "infrastructure resilience",
        ],
        "include": "Protection, disruption, resilience, or control of infrastructure essential to national economic function.",
        "exclude": "Ordinary infrastructure funding updates without resilience, disruption, or strategic-control issues.",
    },
    "Economic Espionage": {
        "keywords": [
            "economic espionage",
            "industrial espionage",
            "cyber espionage",
            "trade secret theft",
            "foreign interference",
            "research theft",
            "intelligence collection",
        ],
        "include": "Espionage, covert acquisition, cyber-enabled theft, or intelligence activity targeting economic or technological advantage.",
        "exclude": "General spying stories without economic, industrial, or technology advantage relevance.",
    },
}

# Topic badges configuration.
# Format: (display_label, background_color, text_color)
TOPIC_BADGE_CONFIGS = {
    "Critical Minerals": ("Critical Minerals", "#e2f0d9", "#385723"),
    "Economic Coercion": ("Economic Coercion", "#ddebf7", "#1f4e78"),
    "Emerging Technologies": ("Emerging Technology", "#f2f2f2", "#7030a0"),
    "Foreign Direct Investment (FDI) Screening": ("FDI Screening", "#fce4d6", "#c65911"),
    "Export Controls and Sanctions": ("Export Controls", "#fce4d6", "#c00000"),
    "Critical Infrastructure Protection": ("Critical Infrastructure", "#ededed", "#333333"),
    "other Critical Dependencies": ("Critical Dependencies", "#f2f2f2", "#555555"),
    "Reshoring and Friendshoring": ("Reshoring & Friendshoring", "#f2f2f2", "#555555"),
    "Weaponisation of Currency": ("Currency Weaponisation", "#f2f2f2", "#555555"),
    "Sovereign Debt and Debt-Trap Diplomacy": ("Debt-Trap Diplomacy", "#f2f2f2", "#555555"),
    "Intellectual Property (IP) Theft": ("IP Theft", "#f2f2f2", "#555555"),
    "Energy Independence and Transition": ("Energy & Transition", "#f2f2f2", "#555555"),
    "Food and Water Security": ("Food & Water Security", "#f2f2f2", "#555555"),
    "Economic Espionage": ("Economic Espionage", "#f2f2f2", "#555555"),
}

# Importance rating label mapping.
IMPORTANCE_RATINGS = {
    5: ("*****", "Must Read"),
    4: ("****", "High Value"),
    3: ("***", "Useful"),
    2: ("**", "Niche"),
    1: ("*", "Low Priority"),
}

MAX_BACKLOG_PER_SOURCE = 1000  # Fail explicitly rather than silently discard overflow.
