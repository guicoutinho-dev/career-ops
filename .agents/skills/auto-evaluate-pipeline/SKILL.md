---
name: auto-evaluate-pipeline
description: Evaluates jobs from the career-ops pipeline, scores them, and generates CVs silently without unnecessary conversational text. Use when user asks to process the pipeline or evaluate jobs.
---

# Auto Evaluate Pipeline

When the user asks you to analyze jobs in the pipeline or find the best opportunities:

## Protocol

1. **Scan & Filter**: Use `node temp_filter.mjs` (or similar filtering logic) to find the top jobs matching the user's profile, ignoring jobs with hard blockers (like strict Citizenship requirements if the user is on a WHV).
2. **Evaluate (Silent)**: 
   - Extract the JD.
   - Generate the evaluation report (`reports/XXX-company-date.md`) following the `modes/oferta.md` format.
   - Add the job to `data/applications.md` with its fit score.
3. **Generate Documents (Silent)**:
   - For any job with a Fit Score >= 4.0, create a custom CV payload (`output/cv-name.json`) following `modes/pdf.md`.
   - Run `node build-cv-html.mjs <input.json> <output.html> <template.html>` to generate the HTML.
   - Run `node generate-pdf.mjs <input.html> <output.pdf> --format=a4 --report=XXX` to generate the ATS-optimized CV PDF.
   - Automatically create a Cover Letter payload (`output/cover-name.json`) based on the evaluation and run `node generate-cover-letter.mjs --payload <input.json> --out <output-cover.pdf>` to generate the Cover Letter PDF.
4. **Respond to User**:
   - DO NOT output the full evaluation report or draft texts in the chat.
   - You MUST output exactly this structure containing all 4 items for the application:
     1. **Link da Vaga**: [Job Title @ Company](URL) (e.g. Fit Score: 4.8 - Reason...)
     2. **Análise da Vaga**: Link to the generated `.md` report.
     3. **CV Gerado**: Link to the generated CV `.pdf`.
     4. **Cover Letter**: Link to the generated Cover Letter `.pdf`.

Keep the interaction extremely concise, acting as a background pipeline processor.
