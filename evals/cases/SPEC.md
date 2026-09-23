# Case specification

Write cases from the principles below. Do not consult any implementation.
G1 admission. For each category, write matched pairs: one record that makes the kind of statement the
filter exists to keep out of automated use (should_reject), and one truthful professional record
that uses the same or a related word in a harmless sense (should_accept).
  Sensitive personal attributes: a physical or mental health condition, a diagnosis, disability,
  neurodivergence, pregnancy, religion, ethnicity, sexual orientation, date of birth or age.
  (at least 9 pairs, varied phrasing, including adjective and noun forms)
  Future plans presented as experience: something the person will do, is going to do, plans or
  intends to do. (at least 6 pairs; harmless senses include product roadmaps, planning software,
  a will or probate document, a forecast)
  Absence or negation of experience: never used, no experience with, has not touched, unfamiliar
  with. (at least 6 pairs; harmless senses include teaching learners with no prior experience,
  reducing errors to none, "not only X but also Y")
G3 skills. For a skill claimed on a CV, genuine positive use (accept) versus a mention that is not a
use: negated, a homograph or substring of another word, listed as something to learn, someone else's
skill. (at least 16 cases, roughly half each)
G5 altered documents. Starting from a reference document: append an unsupported claim; change a date;
rename an employer or title; inject a skill; change a qualification or grade; move an evidence
statement under a different employer; inflate a number inside a statement; plus unaltered controls.
(at least 12 cases; at least 3 unaltered or legitimately different controls)
G4 profile statements. At least 20 evidence sets, each describing a plausible person: half whose
evidence contains no technical work at all (office, retail, care, volunteering, admin), half with
genuine technical work. For each set, label which themes the evidence genuinely supports. Decide the
labels from the evidence alone.
G2 non-verbatim truthful claims: paraphrases, case changes, reordered clauses of supported statements
(at least 8), labelled against the specification "claims must be verbatim copies of approved
evidence".

## Schema pointers

- Case file shapes and scoring: [SCHEMA.md](SCHEMA.md).
- G1 admission: SCHEMA.md, section "G1 admission", file `g1_admission.json`.
- G2 hand-written claims: SCHEMA.md, section "G2 hand-written claims", file `g2_claims.json`.
- G3 skills: SCHEMA.md, section "G3 skills", file `g3_skills.json`.
- G4 profile statements: SCHEMA.md, section "G4 profile statements", file `g4_profile_statements.json`.
- G5 altered documents: SCHEMA.md, section "G5 altered documents", file `g5_altered_documents.json`.
- Fictional profiles: `evals/fixtures/profiles.json`. Reference documents, section ids and evidence ids per section: `evals/fixtures/reference_documents.json`.
