# Hawary Workflow Skills

[English](README.md) | [العربية](README.ar.md)

مهارات سير عمل قابلة لإعادة الاستخدام لوكلاء البرمجة، ومساعدي البرمجة بالذكاء الاصطناعي، والفرق التي تريد عادات تنفيذ مبنية على المصدر.

[![Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-2563eb)](docs/compatible-agents.md)
[![Codex](https://img.shields.io/badge/Codex-ready-111827)](docs/codex.md)
[![Claude](https://img.shields.io/badge/Claude-ready-d97706)](docs/claude.md)
[![Release](https://img.shields.io/github/v/release/mo-hawary/hawary-workflow-skills?color=0ea5e9)](https://github.com/mo-hawary/hawary-workflow-skills/releases)
[![Validate](https://github.com/mo-hawary/hawary-workflow-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/mo-hawary/hawary-workflow-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](LICENSE)

**Hawary Workflow Skills** تجمع سير عمل هندسية متكررة في صيغة يمكن لوكلاء البرمجة قراءتها عبر `SKILL.md`: حالة المستودع، جاهزية سير العمل، تنظيف التوثيق، مواصفات الميزات، تدقيق العقود بين الطبقات، مراجعة طلبات السحب، تخطيط اختبارات QA، وتشغيل اختبارات الموبايل end-to-end باستخدام Maestro.

إذا وفرت لك هذه المهارات وقتا، فضلا ضع نجمة للمستودع. هذا يساعد مطورين آخرين على اكتشافه.

## الاستخدام مع وكيلك

يمكنك توجيه وكيل البرمجة إلى هذا المستودع، أو نسخ مجلدات محددة من `skills/` إلى مجلد المهارات الذي يستخدمه وكيلك. إذا كانت بيئتك تدعم `skills` CLI، يمكنك استخدامه كاختصار اختياري.

| الوكيل | الدليل | مكان التثبيت |
| --- | --- | --- |
| Codex | [التثبيت على Codex](docs/codex.md) | `.agents/skills` أو `~/.agents/skills` أو مجلد مهارات Codex لديك |
| Claude | [التثبيت على Claude](docs/claude.md) | `.claude/skills` أو `~/.claude/skills` أو رفع مهارة مخصصة في Claude |
| OpenClaw و Qwen والوكلاء المتوافقون | [التثبيت على وكلاء آخرين](docs/compatible-agents.md) | أي مجلد متوافق مع Agent Skills |

مثال سريع للنسخ:

```bash
cp -R skills/project-docs-cleanup /path/to/agent/skills/
```

Windows PowerShell:

```powershell
Copy-Item -Recurse skills\project-docs-cleanup C:\path\to\agent\skills\
```

مسار اختياري باستخدام `skills` CLI عندما يدعمه وكيلك:

```bash
npx skills add mo-hawary/hawary-workflow-skills --list
npx skills add mo-hawary/hawary-workflow-skills --skill project-docs-cleanup
```

إذا لم يكن `npx skills` متاحا في بيئتك أو لم يدعم هذا المستودع، استخدم طريقة النسخ أعلاه.

## المهارات

| المهارة | متى تستخدمها | الناتج |
| --- | --- | --- |
| [`project-status-dashboard`](skills/project-status-dashboard/SKILL.md) | عندما تريد معرفة حالة المستودع الآن. | الحالة الحالية، الفرع والانحراف، العمل المفتوح، إشارات التحقق، المخاطر، والخطوة التالية. |
| [`repo-workflow-checker`](skills/repo-workflow-checker/SKILL.md) | عندما تريد معرفة هل المستودع جاهز لعمل متكرر مع الوكلاء. | أصول سير العمل الموجودة، الفجوات، الهيكل المقترح، وخريطة التحقق. |
| [`project-docs-cleanup`](skills/project-docs-cleanup/SKILL.md) | عندما تكون الوثائق أو الخطط أو ملفات backlog أو المواصفات قديمة. | صحة الوثائق، العناصر القديمة، التناقضات، مرشحات الأرشفة، والتعديلات المقترحة. |
| [`feature-spec-delivery-pipeline`](skills/feature-spec-delivery-pipeline/SKILL.md) | عندما تحتاج ميزة أو مجموعة أخطاء إلى خطة قبل البرمجة. | مواصفة مبنية على المصدر، قرارات مطلوبة، تغييرات العقود، مراحل التنفيذ، ومعايير القبول. |
| [`cross-layer-contract-audit`](skills/cross-layer-contract-audit/SKILL.md) | عندما قد تختلف واجهة المستخدم أو API أو قاعدة البيانات أو jobs أو الوثائق. | اختلافات عقود مرتبة حسب الخطورة مع دليل، أثر، اتجاه إصلاح، وتحقق. |
| [`pull-request-review-loop`](skills/pull-request-review-loop/SKILL.md) | عندما يحتاج PR أو فرع إلى مراجعة قوية، تحقق من الإصلاح، وإعادة مراجعة قبل الدمج. | ملاحظات، إثبات الإصلاح، جولات المراجعة، التحقق، المخاطر المتبقية، وجاهزية الدمج. |
| [`qa-bug-hunt-planner`](skills/qa-bug-hunt-planner/SKILL.md) | عندما تحتاج منطقة في المنتج إلى اكتشاف QA ومسارات تدقيق وتذاكر جاهزة للإصلاح. | مسارات تدقيق، مصفوفة ملاحظات، لوحة إصلاحات، بوابات أثر، وخطة اختبار. |
| [`mobile-maestro-e2e-orchestrator`](skills/mobile-maestro-e2e-orchestrator/SKILL.md) | عندما تحتاج اختبار موبايل end-to-end مبني على أدلة باستخدام Maestro. | إعداد التدفق، نتائج نقاط التحقق، أدلة من logs/screenshots/state، تشخيص، ومخاطر متبقية. |

## اختيار المهارة

| إذا كنت تفكر في... | استخدم |
| --- | --- |
| "ما حالة هذا المستودع؟" | `project-status-dashboard` |
| "هل المستودع جاهز لعمل الوكلاء؟" | `repo-workflow-checker` |
| "أي وثائق أصبحت قديمة؟" | `project-docs-cleanup` |
| "خطط هذه الميزة قبل التنفيذ." | `feature-spec-delivery-pipeline` |
| "هل هذه الطبقات متفقة؟" | `cross-layer-contract-audit` |
| "راجع هذا PR بدون نشر تعليقات على GitHub." | `pull-request-review-loop` |
| "ما الذي يجب اختباره قبل الإصلاح؟" | `qa-bug-hunt-planner` |
| "أثبت أن تدفق الموبايل يعمل." | `mobile-maestro-e2e-orchestrator` |

## أمثلة

راجع [docs/examples.md](docs/examples.md) للحصول على prompts جاهزة للنسخ لكل مهارة.

## الهيكل

```text
skills/
`-- skill-name/
    |-- SKILL.md
    |-- agents/
    |   `-- openai.yaml
    `-- references/
        `-- examples.md
```

`SKILL.md` هو المصدر القابل للنقل بين الوكلاء. هذه المهارات Markdown-first، وتستخدم Ruby فقط للتحقق من المستودع. مجلد `references/` يحفظ الأمثلة والقوائم والقوالب خارج جسم المهارة الأساسي. ملف `agents/openai.yaml` هو metadata اختيارية لواجهة OpenAI/Codex، ويمكن للوكلاء الآخرين تجاهله.

## للوكلاء الذكيين

عندما يوجهك مطور إلى هذا المستودع، اقرأ المجلد المناسب داخل `skills/`، ثم حمل فقط الملفات المرجعية التي تحتاجها من مجلد `references/` الخاص بالمهارة. إذا كان ملف مرجعي مفقودا، اذكر الفجوة واستمر من `SKILL.md` الأساسي. لا تفترض مسارات أو منتجات أو أسرار أو قواعد شركة خاصة. اطلب الموافقة قبل تعديل كود التنفيذ عندما تنص المهارة على report-first أو approval-required.

## إضافة مهارات

اتبع نفس سير العمل لكل مهارة جديدة:

1. استخرج السلوك القابل لإعادة الاستخدام من ممارسة حقيقية متكررة.
2. عمم الأسماء والمسارات والأمثلة.
3. اجعل `SKILL.md` مختصرا، وانقل القوالب والقوائم إلى `references/`.
4. أضف metadata في `agents/openai.yaml`.
5. حدث README والوثائق و CHANGELOG.
6. شغل التحقق قبل فتح PR.

## تم اختباره مع

- أوامر النسخ على macOS و Linux
- أوامر النسخ على Windows PowerShell
- تثبيت Codex-style في `.agents/skills`
- تثبيت Claude-style في `.claude/skills`
- هياكل مجلدات Agent Skills العامة

## التحقق

شغل نفس الفحوص المستخدمة في CI:

```bash
ruby scripts/validate_skills.rb
```

يتحقق validator من frontmatter، طول الوصف، التسمية، symlinks، وأنماط النظافة المهمة قبل النشر العام.

## المراجع

- [Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills)
- [Claude Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
- [Skills CLI](https://skills.sh/docs/cli)
- [Vercel Agent Skills directory](https://vercel.com/docs/agent-resources/skills)

## وثائق المشروع

- [الأمثلة](docs/examples.md)
- [خارطة الطريق](docs/roadmap.md)
- [سير عمل تأليف المهارات](docs/skill-authoring.md)
- [المساهمة](CONTRIBUTING.md)
- [الدعم](SUPPORT.md)
- [الأمان](SECURITY.md)
- [مدونة السلوك](CODE_OF_CONDUCT.md)

## سجل التغييرات

راجع [CHANGELOG.md](CHANGELOG.md).

## الرخصة

MIT. راجع [LICENSE](LICENSE).
