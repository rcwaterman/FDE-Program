"""Salesforce client wrapper using simple-salesforce.

Read-only access only: exposes query and download methods, no DML/create/update/delete.
"""

import logging
from typing import Any, Dict, List, Optional
from simple_salesforce import Salesforce

logger = logging.getLogger(__name__)


class SalesforceReadOnlyClient:
    """Wrapper around Salesforce API, exposing only read operations."""

    def __init__(self, username: str, password: str, security_token: str, domain: str = "login.salesforce.com"):
        """Connect to Salesforce with credentials.

        Args:
            username: Salesforce username (email).
            password: Salesforce password.
            security_token: Salesforce security token.
            domain: Salesforce instance domain (e.g. login.salesforce.com for prod, test.salesforce.com for sandbox).
        """
        self.sf = Salesforce(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain,
        )
        logger.info(f"Connected to Salesforce ({domain})")

    def query(self, soql: str) -> List[Dict[str, Any]]:
        """Execute a SOQL SELECT query.

        Args:
            soql: SOQL query string.

        Returns:
            List of record dictionaries.
        """
        logger.debug(f"Executing SOQL: {soql[:100]}...")
        result = self.sf.query(soql)
        records = result.get("records", [])
        logger.info(f"Query returned {len(records)} records")
        return records

    def query_all(self, soql: str) -> List[Dict[str, Any]]:
        """Execute a SOQL SELECT query and handle pagination.

        Args:
            soql: SOQL query string.

        Returns:
            List of all record dictionaries across all pages.
        """
        logger.debug(f"Executing SOQL (with pagination): {soql[:100]}...")
        result = self.sf.query_all(soql)
        records = result.get("records", [])
        logger.info(f"Query returned {len(records)} records (with pagination)")
        return records

    def get_attachment_content(self, version_id: str) -> Optional[bytes]:
        """Download attachment binary content by ContentVersion ID.

        Args:
            version_id: ContentVersion record ID.

        Returns:
            Binary content, or None if not found.
        """
        try:
            logger.debug(f"Fetching attachment content for version {version_id}")
            content = self.sf.ContentVersion.get_base64(version_id, base64_field="VersionData")
            if content:
                logger.info(f"Downloaded attachment {version_id}")
                return content
            logger.warning(f"No content found for version {version_id}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch attachment {version_id}: {e}")
            return None

    def describe(self, sobject: str) -> Dict[str, Any]:
        """Get metadata for a Salesforce object.

        Args:
            sobject: SObject name (e.g. 'Case', 'EmailMessage').

        Returns:
            Object metadata dictionary.
        """
        logger.debug(f"Describing {sobject}")
        return getattr(self.sf, sobject).metadata()
