import sqlite3

import pytest

from src.state import init_state_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
def sample_article():
    return {
        "id": 42,
        "title": "Critical VMware ESXi Zero-Day Exploited in the Wild",
        "url": "https://example.com/article/42",
        "content": (
            "A critical zero-day vulnerability (CVE-2024-12345) in VMware ESXi "
            "has been actively exploited by threat actors targeting enterprise "
            "environments across Southeast Asia. " + "x" * 200
        ),
        "published_at": "2024-03-15T10:00:00Z",
        "feed_name": "BleepingComputer",
        "language": "en",
    }


@pytest.fixture
def sample_analysis():
    return {
        "relevance_score": 9,
        "relevance_reason": "VMware ESXi is in the company tech stack (hypervisor)",
        "severity": "critical",
        "summary_vi": "Phat hien lo hong zero-day nghiem trong trong VMware ESXi.",
        "cve_ids": ["CVE-2024-12345"],
        "affected_products": ["VMware ESXi 8", "VMware ESXi 7"],
        "threat_actors": [],
        "mitre_attack": [
            {
                "tactic": "TA0001 - Initial Access",
                "technique": "T1190 - Exploit Public-Facing Application",
            }
        ],
        "recommendations": [
            "Patch VMware ESXi immediately",
            "Check for IOCs in ESXi logs",
        ],
    }


@pytest.fixture
async def state_db(tmp_path, monkeypatch):
    """Provide a temporary state database."""
    db_path = str(tmp_path / "test_state.db")
    monkeypatch.setattr("src.state.STATE_DB", db_path)
    await init_state_db()
    return db_path


@pytest.fixture
def oksskolten_db(tmp_path):
    """Create a temporary Oksskolten-like SQLite database."""
    db_path = tmp_path / "oksskolten.db"
    conn = sqlite3.connect(str(db_path))

    conn.execute(
        "CREATE TABLE feeds (id INTEGER PRIMARY KEY, title TEXT)"
    )
    conn.execute("""
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            title TEXT,
            url TEXT,
            content TEXT,
            published_at TEXT,
            feed_id INTEGER,
            language TEXT
        )
    """)

    conn.execute("INSERT INTO feeds VALUES (1, 'BleepingComputer')")
    conn.execute("INSERT INTO feeds VALUES (2, 'The Hacker News')")

    long_content = "Critical vulnerability discovered in enterprise software. " * 10
    conn.execute(
        "INSERT INTO articles VALUES (1, 'Article One', 'https://example.com/1', ?, '2024-01-01', 1, 'en')",
        (long_content,),
    )
    conn.execute(
        "INSERT INTO articles VALUES (2, 'Article Two', 'https://example.com/2', ?, '2024-01-02', 2, 'en')",
        (long_content,),
    )
    conn.execute(
        "INSERT INTO articles VALUES (3, 'Article Three', 'https://example.com/3', ?, '2024-01-03', 1, 'vi')",
        (long_content,),
    )
    # Short content — should be skipped by source
    conn.execute(
        "INSERT INTO articles VALUES (4, 'Short', 'https://example.com/4', 'Too short', '2024-01-04', 1, 'en')"
    )

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def company_profile():
    return {
        "company": {
            "name": "Test Corp",
            "sector": ["financial_services"],
            "country": "Vietnam",
        },
        "tech_stack": {
            "hypervisor": ["VMware ESXi 8"],
            "databases": ["PostgreSQL 15"],
        },
        "watched_threat_actors": [
            {"name": "APT32", "alias": "OceanLotus"},
        ],
        "priority_techniques": ["T1190", "T1566"],
        "boost_keywords": ["Vietnam", "banking"],
        "reduce_keywords": ["cryptocurrency mining"],
    }
