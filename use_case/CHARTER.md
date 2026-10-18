# Project charter

<!--
  Fill this in during TC1, from your Getting to Concreteness worksheet.
  Every week's Session 2 notebook reads this file and stops if it is still the
  template. That is on purpose.
-->

**Department:** Payroll

**Working title:** Payroll Submission Automation

---

## The problem

**Who has it?** One real role, not a department.

Payroll Representatives

**What are they trying to do?**

Enter client payroll information into our internal payroll processing system.

**How do they handle it today?**

They manually review the handwritten, unstructured data and then hand-enter it into our system.

**What does that cost — time, money, risk, or relationships?**

This process takes significantly longer for payroll reps, costing them time toward higher value tasks and costing the company operational overhead.

**The problem in one sentence. No solution in it.**

A dramatic reduction in payroll processing time as a result of manual entry for unstructured, handwritten payroll data.

---

## Success

**What does the new world look like for that person?**

The payroll reps will act as humans in the loop, performing a review instead of the data entry.

**How would the firm measure it? Which numbers should move?**

There should be a decrease in the time it takes to process one payroll.

---

## The first product

**Your solution in one sentence.**

A pipeline with a VLM backend that extracts handwritten data and structures it for automated entry into the payroll database.

**Input → Output.** Be specific enough that someone could build it wrong and you
would notice.

| | |
| --- | --- |
| **In** | Client payroll form containing the following handwritten information: Employee Last Name,Pay Rate(Optional),Reg Hours,OT Hours,Vacation Hours,Sick Hours,Holiday Hours,PTO Hours,Other Hours,Cash Tips Amount,Credit Tips Amount,Bonus Amount,Commission Amount,Other Amount,Department (optional),Deduction Code (Optional),Deduction Amount (Optional),Project (optional),Job (optional) |
| **Out** | A csv containing the following information: Employee Last Name,Pay Rate(Optional),Reg Hours,OT Hours,Vacation Hours,Sick Hours,Holiday Hours,PTO Hours,Other Hours,Cash Tips Amount,Credit Tips Amount,Bonus Amount,Commission Amount,Other Amount,Department (optional),Deduction Code (Optional),Deduction Amount (Optional),Project (optional),Job (optional)|

---

## Decisions made later

<!--
  Amended as the cohort goes. Keep the reasoning, not just the choice — Week 5
  asks you to defend your architecture against the options you rejected, and
  Week 10's handoff doc is largely this section.
-->

| Week | Decision | Why, and what you rejected |
| --- | --- | --- |
| 3 | Retrieval approach | <!-- TC3 --> |
| 5 | Agent architecture | <!-- TC5 --> |
| 8 | Model | <!-- TC8 --> |
