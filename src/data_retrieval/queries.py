"""SOQL query constants for Salesforce data extraction.

All queries are read-only SELECT operations. No DML/write operations are used.
Field names are corrected per Salesforce schema inspection (ToAddress, not ToAddresses, etc).
"""

CASES_FOR_SAMPLING = """
SELECT Id, CaseNumber, Subject, Status, Origin, CreatedDate, SourceId
FROM Case
WHERE RecordType.Name = 'Support Request'
  AND Sub_Select_a_Topic__c = 'Regular Payroll'
  AND Origin = 'Email'
  AND SourceId != null
ORDER BY CreatedDate DESC
LIMIT {limit}
"""

EMAIL_MESSAGES_BY_IDS = """
SELECT Id, Subject, TextBody, HtmlBody, FromAddress, ToAddress, CcAddress, BccAddress, CreatedDate, HasAttachment
FROM EmailMessage
WHERE Id IN ({email_ids})
"""

CONTENT_DOCUMENT_LINKS_BY_EMAIL = """
SELECT ContentDocumentId, LinkedEntityId, ContentDocument.Id, ContentDocument.Title,
       ContentDocument.FileType, ContentDocument.ContentSize,
       ContentDocument.LatestPublishedVersionId
FROM ContentDocumentLink
WHERE LinkedEntityId IN ({email_ids})
"""

CONTENT_VERSION_BY_ID = """
SELECT Id, ContentDocumentId, Title, FileType, VersionData, FileExtension
FROM ContentVersion
WHERE Id = '{version_id}'
"""
