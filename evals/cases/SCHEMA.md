# Evaluation case schema

This file defines the exact JSON shape of an evaluation case for each evidence
gate. The principles for writing the cases are in [SPEC.md](SPEC.md). Case
writers need only these two files and the fixtures in `evals/fixtures/`; they
must not read `src/`.

## Fixtures

- `evals/fixtures/profiles.json` holds three fictional candidates. Each entry
  has `profile_id`, `summary`, `job` (the advert used for the reference
  document) and `profile`. The profile ids are `support_to_analytics`,
  `logistics_coordinator` and `junior_developer`.
- `evals/fixtures/reference_documents.json` holds, for each profile, the CV the
  real builder produced from a proposal that copies every admitted statement
  verbatim. Each entry has:
  - `profile_id`, `job` and `protected_profile_hash`;
  - `admitted_evidence`: every statement the builder may use (`id`, `status`,
    `text`);
  - `held_back_evidence`: statements present in the profile but not admitted,
    with the visible reason (status `VERIFY`, sensitive flag, or automatic use
    not allowed);
  - `skills_in_document` and `listed_skills_not_in_document`;
  - `sections`: the document in order. Each section has `section_id`,
    `heading`, optional `subheading`, `category` and `profile_ref`,
    `evidence_ids` (statements printed in it), `section_evidence_ids` (every
    admitted statement that belongs to that employer or project) and
    `paragraphs` (0-based list of printed paragraphs). The `profile` section
    also has `profile_statement_ids`; the `skills` section has `skill_names`;
    `group:*` sections are the empty "Experience", "Selected projects" and
    "Research" headings;
  - `cv_text`, `proposal` and the full `material`.

Profiles must be loaded exactly as stored. The runner checks that the digest of
each loaded profile equals `protected_profile_hash` before running any case.

## Conventions for every case file

- One file per gate: `g1_admission.json`, `g2_claims.json`, `g3_skills.json`,
  `g4_profile_statements.json`, `g5_altered_documents.json`, all in
  `evals/cases/`.
- Top level: `{"gate": "G1", "cases": [ ... ]}`.
- Every case has a unique `case_id` that starts with the gate, for example
  `G1-sensitive-03a`, and a one-sentence `rationale` that explains the label
  from the principle in SPEC.md, not from any implementation.
- `expected` is `"should_reject"` or `"should_accept"` (not used by G4).
- Evidence records written for a case use status `"DIRECT"` and no other flags,
  so that only the text is under test. Their ids take the form
  `"bank:<case_id in lower case>-<n>"`, for example `"bank:g3-07-1"`, so they
  cannot collide with fixture ids.
- Text is plain UTF-8, one professional statement per record, written as it
  would appear on a CV.
- Once written, the case files are frozen: the runner records the SHA-256 of
  each file before its first run and reports rates from the frozen files only.

## G1 admission (`g1_admission.json`)

One evidence record per case. Cases come in matched pairs that share a
`pair_id`.

```json
{
  "case_id": "G1-sensitive-01a",
  "pair_id": "G1-sensitive-01",
  "category": "sensitive",
  "sense": "claim: states a diagnosis",
  "record": {"id": "bank:g1-sensitive-01a-1", "status": "DIRECT", "text": "..."},
  "expected": "should_reject",
  "rationale": "..."
}
```

- `category`: `"sensitive"`, `"future"` or `"absence"`.
- `sense`: a short note of the sense in which the word is used, starting
  `"claim: "` for the should_reject member and `"harmless: "` for the
  should_accept member.
- Scoring: the runner adds the record to a copy of a fixture profile. The case
  is rejected if the record is not among the statements the builder admits.

## G2 hand-written claims (`g2_claims.json`)

A claim a generator might propose for one section of a reference document.

```json
{
  "case_id": "G2-07",
  "profile_id": "junior_developer",
  "section_id": "employment:junior_dev",
  "kind": "paraphrase",
  "source_evidence_ids": ["bank:dev2"],
  "claim": {"text": "...", "evidence_ids": ["bank:dev2"]},
  "truthful": true,
  "expected": "should_reject",
  "rationale": "..."
}
```

- `section_id` must be a section in the profile's reference document whose
  `section_evidence_ids` include the cited ids.
- `kind`: `"paraphrase"`, `"case_change"`, `"reordered_clauses"`,
  `"other_non_verbatim"`, or `"verbatim_control"`.
- `source_evidence_ids`: the admitted statements the claim is derived from.
- `claim.evidence_ids`: the ids the proposal cites, in order. A verbatim claim
  is the cited statements joined by single spaces in the cited order.
- `truthful`: whether a reader would judge the claim true given the source
  statements. Non-verbatim truthful claims are labelled `"should_reject"`,
  because the specification is "claims must be verbatim copies of approved
  evidence". `truthful` lets the report count them separately as a usability
  cost.
- Scoring: the runner takes the reference `proposal`, replaces the named
  section's claims with this single claim and builds the document. The case is
  rejected if the build records a blocked proposal with the same `section_id`
  and the same proposed text.

## G3 skills (`g3_skills.json`)

A skill the CV would list, and the evidence it would rely on.

```json
{
  "case_id": "G3-05",
  "skill": "Kubernetes",
  "category": "negated",
  "evidence": [
    {"id": "bank:g3-05-1", "status": "DIRECT", "text": "..."}
  ],
  "expected": "should_reject",
  "rationale": "..."
}
```

- `category`: `"positive_use"` (should_accept) or one of `"negated"`,
  `"homograph_or_substring"`, `"to_learn"`, `"someone_else"` (should_reject).
- `evidence`: one to three records. The skill name must appear, ignoring case,
  somewhere in at least one record, even if only inside another word, so that
  every case tests a mention rather than an absence.
- Scoring: the runner builds a minimal candidate whose only listed skill is
  `skill` and whose only evidence is `evidence`, and proposes that skill. The
  case is rejected if the skill is absent from the Skills section. The runner
  also records whether each evidence record was admitted, so that a rejection
  caused by admission (G1) is reported separately from one made by the skill
  check.

## G4 profile statements (`g4_profile_statements.json`)

An evidence set describing one plausible person, with labels saying which
profile statements that evidence genuinely supports. G4 does not accept or
reject; it writes statements, and the runner counts statements that the labels
say are unsupported.

```json
{
  "case_id": "G4-03",
  "persona": "Care assistant in a residential home who also runs the rota.",
  "has_technical_work": false,
  "job": {"title": "...", "company": "...", "description": "..."},
  "skills": ["Excel", "Care planning"],
  "evidence": [
    {"id": "bank:g4-03-1", "status": "DIRECT", "text": "..."}
  ],
  "labels": {
    "technical": false,
    "quantitative": false,
    "communication": true,
    "tools": {"Excel": true, "Care planning": true},
    "targeted": {
      "Python implementation": false,
      "API integration": false,
      "AI and language-model tools": false,
      "workflow automation": false,
      "data analysis": false,
      "quantitative research": false,
      "testing and validation": false,
      "customer problem-solving": true
    }
  },
  "rationale": "..."
}
```

- `evidence`: four to eight records about work the person actually did.
  `has_technical_work` is `true` only when that work includes hands-on
  technical work such as building, integrating or testing software or
  technical systems.
- `job`: a realistic advert for a role this person might apply to. The
  `targeted` statement only names capabilities the advert asks for, so write
  adverts that ask, in their own words, for several of the eight capabilities
  below; for people with no technical work, a technical advert is allowed.
- `skills`: the skills the person lists on their profile (two to six).
- `labels`: decided from the evidence alone, before any output is seen. Each
  label answers "would a careful reader say this evidence supports the
  statement?"
  - `technical`: "Practical technical experience spans software development,
    integration and testing."
  - `quantitative`: "Quantitative problem-solving grounded in scientific or
    numerical research." Labels for this theme were written against an
    earlier wording of the same template, and no evidence set in the suite
    generated a quantitative statement, so no reported number depends on it.
  - `communication`: "Customer-facing experience combines clear communication
    with practical problem-solving."
  - `tools`: the statement "Practical technical skills include A, B, C, D."
    names up to four of the listed skills. For every listed skill, `true` if
    the evidence shows the person genuinely using it.
  - `targeted`: the statement "Combines X, Y and Z to solve practical
    problems." names up to three capabilities from the eight listed above. For
    each of the eight, `true` if the evidence supports the person having that
    capability, whatever the advert says.
- Scoring: the runner generates the profile statements for this person and
  advert and counts, per theme, those whose content the labels mark as
  unsupported. It reports that rate on its own, never as a rejection rate.

## G5 altered documents (`g5_altered_documents.json`)

A named alteration applied to a copy of a reference document.

```json
{
  "case_id": "G5-04",
  "profile_id": "logistics_coordinator",
  "alteration": {"type": "set_subheading", "section_id": "employment:transport", "subheading": "2014 – 2020"},
  "expected": "should_reject",
  "rationale": "..."
}
```

The runner deep-copies the reference `material`, applies the alteration to its
`sections`, then recomposes the document text from the altered sections, so
the check sees the altered content rather than a stale text field. Paragraph
indices are 0-based and refer to `sections[].paragraphs` in
`reference_documents.json`. Alteration types:

| `type` | Parameters | Effect |
|---|---|---|
| `unaltered` | none | No change. |
| `append_text` | `section_id`, `paragraph_index`, `text` | Appends a single space and `text` to the paragraph. |
| `replace_paragraph` | `section_id`, `paragraph_index`, `text` | Replaces the paragraph with `text`. |
| `replace_in_paragraph` | `section_id`, `paragraph_index`, `old`, `new` | Replaces the only occurrence of `old` in the paragraph with `new`; `old` must occur exactly once. Use it for inflated numbers and other in-place edits. |
| `set_heading` | `section_id`, `heading` | Replaces the section heading, for example a renamed employer or title (`"Title \| Employer"`). |
| `set_subheading` | `section_id`, `subheading` | Replaces the subheading, for example changed dates (`"2016 – 2020"`, with an en dash and spaces). |
| `inject_skill` | `skill` | Adds the skill to the end of the Skills section, keeping its list and printed line in step. |
| `set_education_paragraphs` | `paragraphs` | Replaces the Education paragraphs with this list. |
| `move_evidence` | `evidence_id`, `from_section_id`, `to_section_id` | Removes the statement from the paragraph that prints it in the first section, adds it as a new last paragraph in the second, and moves its id between the sections' `evidence_ids`. |
| `remove_paragraph` | `section_id`, `paragraph_index` | Deletes the paragraph and drops the ids of the statements it printed. |
| `reorder_paragraphs` | `section_id`, `order` | Reorders the section's paragraphs; `order` is a permutation of their indices. |

- Controls (`should_accept`) are `unaltered` documents and changes that add or
  alter no fact, such as removing or reordering supported paragraphs, or the
  Education form without award dates. In `junior_developer`, the first
  Education line ends with " — Awarded 15 July 2021"; documents created before
  award dates were recorded print the same line without that suffix.
- Scoring: the case is rejected if whole-document validation fails.
