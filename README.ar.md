<div dir="rtl" lang="ar-EG">

# Hawary Workflow Skills

[English](README.md) | [مصري](README.ar.md)

مهارات جاهزة تقدر تستخدمها مع coding agents زي Codex و Claude وأي agent بيفهم صيغة `SKILL.md`.

[![Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-2563eb)](docs/compatible-agents.md)
[![Codex](https://img.shields.io/badge/Codex-ready-111827)](docs/codex.md)
[![Claude](https://img.shields.io/badge/Claude-ready-d97706)](docs/claude.md)
[![Release](https://img.shields.io/github/v/release/mo-hawary/hawary-workflow-skills?color=0ea5e9)](https://github.com/mo-hawary/hawary-workflow-skills/releases)
[![Validate](https://github.com/mo-hawary/hawary-workflow-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/mo-hawary/hawary-workflow-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](LICENSE)

<p dir="rtl"><strong dir="ltr">Hawary Workflow Skills</strong> فكرته بسيطة: بدل ما تشرح للـ agent نفس طريقة الشغل كل مرة، تديه skill جاهزة تمشيه بخطوات واضحة.</p>

المهارات دي بتساعدك في حاجات زي:

- تعرف حالة repo بسرعة.
- تنظف docs وتطلع الحاجات القديمة.
- تكتب spec واضح قبل التنفيذ.
- تراجع PRs بشكل منظم.
- تخطط QA bug hunts.
- تشغل mobile E2E flows باستخدام Maestro.

لو المهارات دي وفرت عليك وقت، اعمل star للـ repo. ده بيساعد ناس أكتر تكتشفه.

## إحنا فين دلوقتي

الـ repo ده public، برخصة MIT، و `main` عليه protection والتغييرات بتدخل عن طريق pull requests. المصدر الأساسي للمهارات موجود في `skills/`، ومعاه README بالإنجليزي والمصري، أدلة تثبيت لـ Codex و Claude، ملاحظات للـ agents المتوافقة، أمثلة، roadmap، changelog، و CI validation.

## استخدمه مع الـ agent بتاعك

تقدر تعمل واحد من دول:

- تخلي الـ coding agent يقرأ repo ده مباشرة.
- تنسخ skill معينة من `skills/` لمجلد skills عندك.
- تستخدم `skills` CLI لو بيئتك بتدعمه.

| الـ agent | الدليل | مكان التثبيت |
| --- | --- | --- |
| Codex | [Install on Codex](docs/codex.md) | `.agents/skills` أو `~/.agents/skills` أو skills directory الخاص بـ Codex |
| Claude | [Install on Claude](docs/claude.md) | `.claude/skills` أو `~/.claude/skills` أو Claude custom Skills upload |
| OpenClaw و Qwen وأي agent متوافق | [Install on other agents](docs/compatible-agents.md) | أي folder متوافق مع Agent Skills |

مثال سريع للنسخ:

</div>

```bash
cp -R skills/project-docs-cleanup /path/to/agent/skills/
```

<div dir="rtl" lang="ar-EG">

Windows PowerShell:

</div>

```powershell
Copy-Item -Recurse skills\project-docs-cleanup C:\path\to\agent\skills\
```

<div dir="rtl" lang="ar-EG">

اختياري: لو الـ agent عندك بيدعم `skills` CLI:

</div>

```bash
npx skills add mo-hawary/hawary-workflow-skills --list
npx skills add mo-hawary/hawary-workflow-skills --skill project-docs-cleanup
```

<div dir="rtl" lang="ar-EG">

لو `npx skills` مش متاح أو مش شغال مع البيئة عندك، استخدم طريقة النسخ اللي فوق.

## المهارات الموجودة

| Skill | تستخدمها إمتى؟ | بتطلع إيه؟ |
| --- | --- | --- |
| [`project-status-dashboard`](skills/project-status-dashboard/SKILL.md) | لما تحتاج تعرف حالة repo بسرعة. | الحالة الحالية، branch/drift، الشغل المفتوح، verification hints، risks، والخطوة الجاية. |
| [`repo-workflow-checker`](skills/repo-workflow-checker/SKILL.md) | لما تحب تعرف repo جاهز لشغل agents بشكل منظم ولا لأ. | الموجود، النواقص، scaffold مقترح، وخريطة verification. |
| [`project-docs-cleanup`](skills/project-docs-cleanup/SKILL.md) | لما docs أو plans أو backlog أو specs يكونوا محتاجين cleanup. | doc health، حاجات قديمة، تناقضات، archive candidates، وتعديلات مقترحة. |
| [`feature-spec-delivery-pipeline`](skills/feature-spec-delivery-pipeline/SKILL.md) | لما feature أو bug cluster محتاج spec قبل الكود. | source-backed spec، قرارات محتاجة تأكيد، contract changes، phases، و acceptance criteria. |
| [`cross-layer-contract-audit`](skills/cross-layer-contract-audit/SKILL.md) | لما UI/API/database/jobs/docs ممكن يكونوا مش متفقين. | contract mismatches مرتبة بالخطورة، evidence، impact، fix direction، و verification. |
| [`pull-request-review-loop`](skills/pull-request-review-loop/SKILL.md) | لما PR أو branch محتاج review قوي و re-review قبل merge. | findings، fix proof، review rounds، validation، residual risk، و merge readiness. |
| [`qa-bug-hunt-planner`](skills/qa-bug-hunt-planner/SKILL.md) | لما area محتاجة QA discovery وخطة bugs جاهزة للإصلاح. | audit tracks، findings matrix، fix board، impact gates، و test plan. |
| [`mobile-maestro-e2e-orchestrator`](skills/mobile-maestro-e2e-orchestrator/SKILL.md) | لما تحتاج mobile E2E proof باستخدام Maestro. | flow setup، checkpoint results، logs/screenshots/state evidence، diagnosis، و residual risk. |

## تختار أنهي skill؟

| لو بتفكر تقول... | استخدم |
| --- | --- |
| "إيه حالة repo ده؟" | `project-status-dashboard` |
| "هل repo جاهز لشغل agents؟" | `repo-workflow-checker` |
| "إيه docs اللي بقت قديمة؟" | `project-docs-cleanup` |
| "خطط feature دي قبل التنفيذ." | `feature-spec-delivery-pipeline` |
| "هل layers دي متفقة؟" | `cross-layer-contract-audit` |
| "راجع PR ده من غير ما تنشر comments على GitHub." | `pull-request-review-loop` |
| "نختبر إيه قبل ما نصلح؟" | `qa-bug-hunt-planner` |
| "اثبت إن mobile flow ده شغال." | `mobile-maestro-e2e-orchestrator` |

## أمثلة

شوف [docs/examples.md](docs/examples.md) عشان prompts جاهزة لكل skill.

## شكل المجلدات

</div>

```text
skills/
`-- skill-name/
    |-- SKILL.md
    |-- agents/
    |   `-- openai.yaml
    `-- references/
        `-- examples.md
```

<div dir="rtl" lang="ar-EG">

`SKILL.md` هو المصدر الأساسي اللي أي agent يقدر يقرأه. المهارات دي Markdown-first، و Ruby مستخدمة بس للتحقق من repo. مجلد `references/` فيه الأمثلة والـ checklists والـ templates بعيد عن جسم skill الأساسي. ملف `agents/openai.yaml` اختياري ومفيد كـ metadata لـ OpenAI/Codex، وأي agent تاني يقدر يتجاهله.

## تعليمات للـ AI agents

لو developer وجهك للـ repo ده، اقرأ skill المناسبة من `skills/`، وبعدها افتح بس الملفات اللي محتاجها من `references/`. لو reference file مش موجود، قول إن فيه gap وكمل من `SKILL.md`. ما تفترضش private paths أو product names أو secrets أو rules خاصة بشركة. لو skill بتقول report-first أو approval-required، اسأل قبل ما تعدل implementation code.

## إضافة skills جديدة

كل skill جديدة تمشي بنفس workflow:

1. استخرج workflow متكرر ومفيد من شغل حقيقي.
2. عمم الأسماء والمسارات والأمثلة.
3. خلي `SKILL.md` مختصر، وانقل templates/checklists لـ `references/`.
4. أضف metadata في `agents/openai.yaml`.
5. حدّث README والdocs والـ changelog.
6. شغّل validation قبل ما تفتح PR.

## متجرب على

- أوامر نسخ macOS و Linux
- أوامر نسخ Windows PowerShell
- تثبيت Codex-style في `.agents/skills`
- تثبيت Claude-style في `.claude/skills`
- أي generic Agent Skills folder layout

## التحقق

شغّل نفس check اللي CI بيشغله:

</div>

```bash
ruby scripts/validate_skills.rb
```

<div dir="rtl" lang="ar-EG">

الـ validator بيتأكد من frontmatter، طول description، naming، symlinks، وحاجات مهمة للنشر العام.

## مراجع

- [Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills)
- [Claude Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
- [Skills CLI](https://skills.sh/docs/cli)
- [Vercel Agent Skills directory](https://vercel.com/docs/agent-resources/skills)

## Docs المشروع

- [Examples](docs/examples.md)
- [Roadmap](docs/roadmap.md)
- [Skill authoring workflow](docs/skill-authoring.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Security](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)

## Changelog

شوف [CHANGELOG.md](CHANGELOG.md).

## License

MIT. شوف [LICENSE](LICENSE).

</div>
