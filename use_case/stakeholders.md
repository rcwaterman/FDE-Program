# Stakeholders

Everyone you talk to about this project, from the first "where would this even
run?" to the final readout.

> **Week 10's technical challenge is a report to the people in this file.**
> You cannot write it later from memory. Add a row the day you have the
> conversation.

Use roles, not names. This is a public repo.

| Week | Who (role) | What you asked | What you learned | Follow-up |
| --- | --- | --- | --- | --- |
| 1 | MLOps Engineer | What is the optimal extraction method for phase 1 of the project? | A targeted OCR model can extract text in regions of the image and raise an exception when the bounding box of the text exceeds the target region. | TBD |
| 2 | Payroll Manager | How are payroll import files loaded into Gold? | The payroll rep downloads the file from the Salesforce case and imports it into Gold during Step 2 using the Import Payroll option. | Design the automation to produce an import-ready file for the Salesforce case. |
| 2 | Payroll Manager | What happens before the payroll file is imported? | In Step 1, the rep verifies the pay date and pay period, makes any necessary setup adjustments, and creates the payroll entries. | Account for Step 1 in the automated workflow. |
| 2 | Payroll Manager | Would converting handwritten timesheets into import files fit the current workflow? | Yes. Creating the import file automatically could significantly reduce manual data entry, especially for larger clients. | Test generated import files using actual payroll inputs. |
| 2 | Payroll Manager | What manual work remains when an import file is used? | One-off items may still require manual entry, and separate checks are not handled by the standard import. | Identify exceptions that should remain human-managed. |
| 2 | Payroll Manager | Does Gold validate imported payroll data? | Gold flags certain errors, such as missing pay rates when hours are present, but it does not detect every unusual or incorrect value. | Determine what additional validation should happen before creating the import file. |
| 2 | Payroll Manager | How is payroll reviewed before submission? | The payroll rep verifies the totals, and some clients also review and approve a payroll journal before finalization. | Preserve human review and verification in the automated process. |
| 2 | Payroll Manager | What identifies the employee when data is imported? | The employee number determines which employee receives the imported payroll information. | Validate employee numbers before generating the import file. |
| 2 | Payroll Manager | How quickly do we need to process a new Salesforce case? | Cases may be picked up within an hour on slower days or within a few hours on busy days. | Process new cases quickly enough for the output to be available before the payroll rep begins working them. |
| 2 | Payroll Manager | Does the payroll rep work directly from the Salesforce case? | Yes. The payroll request enters Salesforce and is routed to a payroll rep, who works from the case and its attachments. | Use the Salesforce case as the handoff point for the automation output. |
| 2 | Payroll Manager | How are unclear handwritten instructions handled? | Payroll specialists use their judgment to interpret handwritten instructions and contact the client when something cannot be understood. | Route ambiguous or low-confidence handwriting for human review. |
| 3 | Payroll Manager | Is extracting handwritten timesheets with a model and storing the results in the import template the right approach? | Yes. The payroll manager says this would be a huge time saver, especially for large clients that still hand-key timesheets. Anything that speeds up the process benefits the team. | Prioritize clients with the largest handwritten timesheets for the first test. |
| 3 | Payroll Manager | What happens when fields in the import file are blank? | A blank employee number means no data is imported for that employee. A blank hours field produces a zero check, so no pay is issued. Blank optional fields such as pay rate or department use the values already on the employee profile. | Define how blank fields are handled in the generated import file and flag blank employee numbers for review. |
| 3 | Payroll Manager | Is a partially pre-filled import template still useful when handwritten hours are blank? | Yes. Keying hours into a one-page spreadsheet is still faster than working in the payroll system. The payroll manager sometimes types directly into the import spreadsheet for speed. | Pre-fill typed fields and leave handwritten hours blank for the rep to complete. |
| 3 | Payroll Manager | Does the default import template already include employee information? | Yes. The default import file lists all active employees with their employee number and name. It does not include pay rate or department unless those values change for the payroll. | Generate the pre-filled template from the same employee data the default import uses. |
| 3 | Payroll Manager | Can Social Security numbers be used as employee identifiers? | Gold can technically use Social Security numbers, but clients are not asked to send them by email. | Use employee numbers as the identifier and keep Social Security numbers out of emailed outputs. |

---

## Not everyone is an infrastructure contact

The useful list is wider than it first looks, and Week 10's report goes to all of
them:

- **The person with the problem** — the one from your charter. Have you shown
  them anything yet?
- **Whoever owns the data** you would need.
- **Whoever runs the platform** your app would deploy onto.
- **Whoever approves** a deployment, a spend, or a vendor.
- **Your own manager**, who will be asked whether this was worth the ten weeks.

If a week goes by with no conversation, add a row saying so and why. A visible
gap is more useful than a silent one.
