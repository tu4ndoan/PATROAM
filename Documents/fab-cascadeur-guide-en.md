---
title: "From Animation to Income: A Guide to Selling 3D Assets on FAB (with Cascadeur)"
slug: "guide-selling-3d-assets-on-fab"
excerpt: "The full creator journey for 3D assets: rig-light animation in Cascadeur, opening a FAB seller account, packaging & publishing products, getting paid, and handling withholding tax (W-8BEN)."
category: "Game Dev"
tags: ["Cascadeur", "FAB", "3D Assets", "Game Dev", "Animation", "Tax"]
readingTime: 10
featured: true
publishedAt: "2026-07-25"
seoTitle: "How to make animations in Cascadeur & sell 3D assets on FAB (A-Z)"
seoDescription: "End-to-end guide: create animations in Cascadeur, set up a FAB seller account, package and publish products, earn 88% revenue, and handle W-8BEN withholding tax as a seller in Vietnam."
---

# From Animation to Income: A Guide to Selling 3D Assets on FAB (with Cascadeur)

If you make 3D assets — characters, animations, environments, props — then **FAB** (Epic Games' official content marketplace, which merged the Unreal Marketplace, Sketchfab, Quixel and ArtStation Marketplace) is the most natural place to sell today. This post walks the full loop: **make an animation → open a seller account → publish a product → get paid → handle tax.**

> ⚠️ **Quick note:** The tax section below is general knowledge, not personalized tax advice. Numbers and procedures change over time — always double-check FAB's official docs and consult an accountant before filing.

---

## 1. Making animations quickly in Cascadeur

[Cascadeur](https://cascadeur.com/) is **AI-assisted** keyframe animation software, strongest for physics-based motion (jumps, punches, falls, action). The best part: you don't need complex rigging to get "real" motion, thanks to a few AI features:

- **AutoPosing** — adjust a few control points and the AI balances the whole body into a natural pose.
- **AutoPhysics** — analyzes motion by gravity/force/momentum and adds believable secondary motion (an arm swing on a jump, the follow-through of a kick).
- **AI Inbetweening** (since 2025.1) — auto-generates the in-between frames connecting your key poses for smoother transitions.

### The core workflow (blocking → physics)

1. **Install & open a project** — grab the Free build (fine for solo work) from the site, then import your model/rig or use a sample character.
2. **Blocking** — set the key poses on the timeline: start, peak, end.
3. **AutoPosing** — use it to quickly lock each pose into a balanced, on-center stance.
4. **Spacing & timing** — adjust frame spacing to shape the rhythm of the motion.
5. **AutoPhysics** — enable it to add momentum and secondary motion; fine-tune with the sliders/filters.
6. **Polish & export** — smooth things out, then export to `.fbx` for Unreal/Unity or for packaging into a product.

### Videos to watch (easy to advanced)

- 🎬 [Get Started in Cascadeur — Your First Animation](https://www.youtube.com/watch?v=nUADUrQf97c) — official, ~13 min, your first animation from scratch.
- ⚡ [Your first Cascadeur animation in 5 MINUTES](https://www.youtube.com/watch?v=dztpzcyXMDw) — super fast, 3 basic animations.
- 📚 [Everything About Cascadeur (Full Free Course)](https://www.youtube.com/watch?v=vPwVGuYEk2o) — ~26 min crash course: interface, posing, tools, workflow.
- 🧪 [Everything about AutoPhysics (Physics Pipeline 2025)](https://www.youtube.com/watch?v=QPPIxzbH1o8) — deep dive on the physics tools.
- 📺 [Official Cascadeur tutorial playlist](https://www.youtube.com/playlist?list=PLXcuot7sDvCtyH16zo2f3JJFlNGNfLzwg) and the learning hub at [cascadeur.com/tutor](https://cascadeur.com/tutor).

---

## 2. Creating a seller account on FAB.com

Selling on FAB goes through an **Epic Games** account. The steps:

1. **Register / sign in to Epic Games** at [fab.com](https://www.fab.com/) — email, or link Google/Apple.
2. Click **“Publish”** in the toolbar to start the become-a-seller flow.
3. **Accept the Fab Distribution Agreement.** Read the terms carefully before agreeing.
4. **Create your Creator Code** — a unique code/username that also becomes your publisher page URL (`fab.com/sellers/<creator-code>`).
5. **Complete your Publisher Profile** — avatar, display name, bio.
6. **Trader Verification** — verify your seller identity, then complete your **tax** and **payout** details. Specific requirements vary by country (see section 4).

> 💡 Have these ready: your ID document, your payout account info (PayPal/bank per FAB's supported methods for VN), and the details needed to fill out the W-8BEN tax form.

---

## 3. Packaging, publishing & getting paid

### 3.1. Packaging your product

- **Standardize your files**: export the right formats (`.fbx`, `.glb`, textures `.png/.tga`, materials...). For Unreal assets, package to the correct engine project/plugin structure.
- **Clean, clear naming**: consistent file/folder names, remove leftover and temp files.
- **Include docs**: a short README (how to import, scale, license) reduces refunds and earns better reviews.
- **Check the rights**: every texture, sound, and model inside must be yours or licensed for resale.

### 3.2. Publishing (listing)

1. **Create a Listing** in the publisher dashboard.
2. **Upload asset(s)** — a listing can bundle multiple assets into one product.
3. **Media set** — required: a **thumbnail** + **at least one more image or a 3D preview**. Good images massively lift conversion; include a nice render plus a wireframe/scale-reference shot.
4. **Fill in the details**: title, description, tags, category, compatible engines, **price** (or Free), license.
5. **Submit for review** — FAB reviews before it goes live. Only products that pass technical & content checks get published.

### 3.3. Getting paid (payout)

- **Revenue split**: you keep **88% of the revenue** from your product sales (FAB keeps 12%).
- **Payout threshold**: FAB pays out **~30 days after month-end**, but only once your balance reaches **$100 USD or more**. Below that, it rolls over to later months.
- **Refunds**: approved refunds are **deducted from your next payout**.

> Example: a $20 product → you receive ~$17.6 per sale (before the withholding tax in section 4).

---

## 4. Income tax from FAB

This is the part many people skip and then feel "short-changed" at payout. There are **two layers of tax**:

### 4.1. US withholding tax — the W-8BEN form

Epic/FAB treats asset sales as **royalties**. Sellers **outside the US** must file:

- **Form W-8BEN** — for foreign **individuals**.
- **Form W-8BEN-E** — for foreign **businesses/entities**.

This form confirms you're not a US taxpayer and sets the **withholding rate on the US-sourced portion of your revenue** (purchases by buyers in the US).

**Key points for sellers in Vietnam:**
- **Vietnam currently has NO income tax treaty in force with the US.** So you **don't get a treaty-reduced rate**, and the **US-sourced** portion of your revenue is typically withheld at the default **30%**.
- The **non-US-sourced** portion (buyers elsewhere) is **not** subject to US withholding.
- Filling out W-8BEN **accurately and completely** still matters: incorrect or blank forms can get your entire payout held or withheld more heavily. (Many sellers report getting stuck at tax setup due to an incorrectly generated form — be patient and redo it under the correct individual/business type.)

### 4.2. Income tax in Vietnam

FAB income is **foreign-sourced income** and, in principle, **must be declared in Vietnam**:

- An individual earning money online usually falls under **personal income tax / household business** — typically **VAT + PIT** at a percentage of revenue (the exact rate depends on revenue thresholds and how you register).
- Keep **all records**: FAB payout statements, amounts already withheld in the US, exchange rates — for filing and to avoid back-tax risk.
- Because there's no VN–US treaty, tax already withheld in the US is **hard to credit** against your VN tax → all the more reason to plan carefully to avoid double taxation.

> ✅ **Practical recommendation:** as soon as revenue becomes steady, (1) fill W-8BEN correctly from the start, (2) register the appropriate business/filing form in Vietnam, and (3) meet an accountant familiar with foreign-sourced income tax. The consulting fee is far smaller than penalties/back taxes.

---

## Summary

| Step | What to do | Key takeaway |
|------|------------|--------------|
| 1. Animation | Blocking → AutoPosing → AutoPhysics → export FBX | The free Cascadeur build is enough to start |
| 2. Account | Publish → accept agreement → Creator Code → verify | Uses your Epic Games account |
| 3. Publish | Clean packaging → great media → submit for review | Keep **88%**, payout at ≥ **$100**, ~30 days after month-end |
| 4. Tax | W-8BEN (individual) / W-8BEN-E (business) + file in VN | No VN–US treaty → ~**30%** withheld on the US-sourced portion |

Making great assets is only half the story — the other half is building a clean selling process and managing your cash flow. Here's to shipping your first product soon! 🚀

---

*References:*
- *[Fab — Publisher Get Started](https://dev.epicgames.com/documentation/fab/publisher-get-started-in-fab)*
- *[Fab Distribution Agreement](https://www.fab.com/distribution-agreement)*
- *[Epic to unify content marketplaces (88% revenue)](https://www.gamedeveloper.com/marketing/epic-to-unify-content-marketplaces-and-offer-creators-88-percent-revenue-cut)*
- *[Cascadeur — Learn / Tutorials](https://cascadeur.com/tutor)*
