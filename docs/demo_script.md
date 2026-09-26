# 5-minute viva demo script

**Before the viva (30 min earlier):** `make up`, wait for `https://localhost/api/v1/ready` to show
`"models_loaded": true`, `make seed-demo` (prints the demo password; keep it at hand), open https://localhost in a
browser and accept/trust the local certificate. Have one extra `.tiff` slide on the desktop for the live upload.

| Time | Do | Say |
|---|---|---|
| 0:00 | Show the login page, sign in as `pathologist.radboud@gleasonai.demo` | "This is the telehealth review layer of the thesis. Clinicians sign in with hospital accounts; sessions are httpOnly cookies with a 15-minute idle timeout." |
| 0:30 | Dashboard | "Each clinician sees only their hospital. The queue puts the highest P(csPCa) first; low-confidence results are flagged for mandatory review." |
| 1:00 | *New upload*, pseudonym `VIVA-01`, drop the slide, *Upload and analyse* | "Only a pseudonym - no names. The slide is validated, stored encrypted and never leaves this server." |
| 1:30 | Case page: live steps | "The worker runs exactly the notebook pipeline: tissue mask, 224-px tiles, Phikon features, the five FedAvg models, probability averaging." |
| 2:15 | Open a finished demo case (or the new one) | "ISUP grade with its probability, the Youden and 95 %-sensitivity thresholds from the thesis, and the model version hash." |
| 2:45 | Toggle the heatmap, change opacity, click top tile #1 | "Attention shows which regions drove the decision; clicking a tile jumps to it at full resolution." |
| 3:15 | Review: *Amend grade*, choose a grade, write a comment, submit | "The AI is decision support: a pathologist confirms, amends or rejects, with a mandatory comment when disagreeing." |
| 3:45 | *Download PDF report* | "The report carries the disclaimer, the review and the model version." |
| 4:05 | Sign out, sign in as `admin@gleasonai.demo`, open the case -> *Audit trail*; Admin -> *Audit trail* -> *Verify integrity* | "Every step is logged in an append-only, hash-chained audit trail." |
| 4:35 | *Model card* | "The deployed ensemble reproduces the thesis exactly: csPCa AUC 0.9705, cancer AUC 0.9935, QWK 0.9047 on 2,124 test slides - with its limitations: two hospitals, PANDA only." |
| 5:00 | End | |

Fallback if the live upload is slow: the six demo cases are already processed - open one of them.
