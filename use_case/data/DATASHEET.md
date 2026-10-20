# Datasheet

## What this data is

**Generated on:** 10/20/2026
**Generated from:** Payroll timesheet template and axes.md variation definition.
**Images:** 100

## Variation axes

<!-- What you varied to get coverage: persona, format, difficulty, edge cases. -->

| Axis | Values | Variation |
| --- | --- | --- |
| Readability | read, low_confidence, unreadable | The variation in this axis comes from legibility of the handwriting from the user. This could include poor handwriting, poor scan quality of the document, or struckthrough/scribbled out text. |
| Document Orientation | 0-360 degrees | The document can be oriented in any direction relative to the standard landscape format. |
| Document Scale | 0.4x-1x | The timesheet could be submitted as an image with variation in the scale relative to a scanned document. A 1x scale document is an image where the entire bounds of the image are the timesheet, where a 0.4x scale image is scaled such that 40% of the image area is the timesheet. |
| Document Perspective | 0-50 degrees | This axis is the relative angle of the timesheet to the perspective of the camera. Zero degrees would be perfectly flat, where 50 degrees represents a 50 degree angle of the timesheet page relative to the camera. |
| Template Content | N/A | The template can contain an infinitely variable set of content across each of the fields. The generated text and handwriting should feature names that look real and pay rates/hours across a statistically probable distribution based on real labor and pay statistics. Pay types should also be varied based on the set defined in the template. |

## What it can validate

1. Preprocessing pipeline accuracy.
2. Readability classification accuracy.
3. Handwriting extraction accuracy in expected fields.
4. Accuracy of data import into template.

## What it cannot validate

1. Semantic modifications to the template format (i.e. someone handwriting that the OT Hours column is actually Tips).
2. Unexpected handwritten notes (i.e. "send employee X an additional check for $500)

## What it must never be compared against

The actual data pulled from Salesforce.

## Leakage check — outward (what left)

- [x] No real record was used as a seed.
- [x] No real entity names, IDs, or identifiers appear.
- [x] No distinctive internal formats that would identify the firm.
- [x] Rare categories were generated, not copied.

All of the data generated for this project was generated using a blank payroll template image. A new Claude session was created without any context from any local files or folders, and Claude was instructed to only generate images with variations defined by the axes in the axes.md file.

## Leakage check — inward (what the eval shares with the training data)

**Are the goldens disjoint from anything the system was, or will be, tuned on?**
Yes

**Overlap between `golden.jsonl` and this file, by the dedupe measure:** 
0 rows deduped. The "golden" synthetic data is not present anywhere in the newly generated synthetic data.
