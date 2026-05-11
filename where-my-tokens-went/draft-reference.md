---
title: "Where My Tokens Actually Went"
date: 2026-05-09
tags: [ai, agents, llm, analysis]
description: "I spent $200/month on Codex. At API rates, I used $15,000 worth of compute in 3 months."
---

<style>
.stat-hero {
  text-align: center;
  padding: 2.5rem 1rem;
  margin: 2rem 0;
  background: #f8f9fa;
  border-radius: 8px;
}
.stat-hero .number {
  font-size: 4.5rem;
  font-weight: 800;
  line-height: 1;
  color: #1a1a2e;
}
.stat-hero .label {
  font-size: 1.1rem;
  color: #555;
  margin-top: 0.5rem;
}
.stat-hero .detail {
  font-size: 0.9rem;
  color: #888;
  margin-top: 0.25rem;
}
.stat-row {
  display: flex;
  gap: 1rem;
  margin: 1.5rem 0;
  flex-wrap: wrap;
}
.stat-card {
  flex: 1;
  min-width: 140px;
  background: #f8f9fa;
  border-radius: 6px;
  padding: 1.2rem;
  text-align: center;
}
.stat-card .number {
  font-size: 1.8rem;
  font-weight: 700;
  color: #1a1a2e;
}
.stat-card .label {
  font-size: 0.8rem;
  color: #666;
  margin-top: 0.25rem;
}
.bar-chart {
  margin: 1.5rem 0;
}
.bar-row {
  display: flex;
  align-items: center;
  margin-bottom: 0.6rem;
}
.bar-label {
  width: 120px;
  font-size: 0.85rem;
  color: #333;
  flex-shrink: 0;
  text-align: right;
  padding-right: 0.75rem;
}
.bar-track {
  flex: 1;
  background: #eee;
  border-radius: 4px;
  height: 24px;
  position: relative;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 4px;
  background: #4a6fa5;
  display: flex;
  align-items: center;
  padding-left: 8px;
  font-size: 0.75rem;
  color: white;
  font-weight: 600;
  white-space: nowrap;
}
.bar-fill.highlight {
  background: #c44e52;
}
.bar-fill.muted {
  background: #93b5c6;
}
.bar-value {
  width: 80px;
  font-size: 0.8rem;
  color: #666;
  text-align: right;
  padding-left: 0.5rem;
  flex-shrink: 0;
}
.comparison-table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.5rem 0;
  font-size: 0.9rem;
}
.comparison-table th {
  text-align: left;
  padding: 0.6rem 0.75rem;
  border-bottom: 2px solid #ddd;
  font-weight: 600;
  color: #333;
}
.comparison-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #eee;
}
.comparison-table tr:last-child td {
  border-bottom: none;
}
.comparison-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.comparison-table .highlight-cell {
  font-weight: 600;
  color: #c44e52;
}
.cost-ramp {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 160px;
  margin: 1.5rem 0;
  padding: 0 0.5rem;
}
.cost-bar {
  flex: 1;
  background: #4a6fa5;
  border-radius: 3px 3px 0 0;
  position: relative;
  min-width: 20px;
}
.cost-bar-label {
  position: absolute;
  bottom: -1.8rem;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.65rem;
  color: #888;
  white-space: nowrap;
}
.cost-bar-value {
  position: absolute;
  top: -1.4rem;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.7rem;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
}
.cost-bar.peak {
  background: #c44e52;
}
.vs-block {
  display: flex;
  gap: 1.5rem;
  margin: 1.5rem 0;
  flex-wrap: wrap;
}
.vs-side {
  flex: 1;
  min-width: 200px;
  padding: 1.2rem;
  border-radius: 6px;
  text-align: center;
}
.vs-side.good {
  background: #e8f5e9;
}
.vs-side.bad {
  background: #fce4ec;
}
.vs-side .number {
  font-size: 2rem;
  font-weight: 700;
}
.vs-side .label {
  font-size: 0.85rem;
  color: #555;
  margin-top: 0.25rem;
}
.vs-side .sublabel {
  font-size: 0.75rem;
  color: #888;
}
.callout {
  background: #f0f4f8;
  border-left: 3px solid #4a6fa5;
  padding: 0.75rem 1rem;
  margin: 1.5rem 0;
  font-size: 0.9rem;
  color: #444;
}
</style>

# Where My Tokens Actually Went

I'm on the Codex $200/month plan. A few weeks ago I opened the dashboard and my usage had dropped from 60% to 10% overnight.

I'd just finished a large refactor of [meridian](https://github.com/meridian-flow/meridian-cli), a multi-agent orchestration engine I've been building. The refactor itself was driven by the engine — product leads spawning design leads spawning architects spawning coders, all coordinating through the same system they were restructuring. Dozens of agents running in parallel across Claude Code, Codex, and OpenCode. The largest session to date — over 8 hours. I'd been regularly running 2-3 hour sessions without thinking much about it, but this one was different.

The timing lined up with a wave of complaints I was seeing from Claude Code and Codex users about providers getting stingy with usage limits. Claude Code users had been hitting it for months. Now with GPT-5.5 bringing a flood of new Codex users, the same thing was starting there. My first thought was the same as everyone else's: did they quietly reduce my limits?

I also had no real visibility into where the token budget was going — how much cache I was hitting, whether cost correlated with useful work, or how much was just overhead from the multi-agent setup. So I wrote a script to pull the tool call history from every agent session I could find — Claude Code's JSONL logs, Codex's native session files, OpenCode's SQLite database. Every `Read` call, every `cat` and `head` in bash, every grep. For each file read, I tracked whether the agent had already seen that file earlier in the same session.

10,163 sessions total. February through May 2026. Roughly a dozen models.

<div class="stat-hero">
  <div class="number">79%</div>
  <div class="label">of file reads were redundant</div>
  <div class="detail">114,922 out of 146,012 reads — within the same session</div>
</div>

Not across sessions — within a single session. The agent re-reading a file it already had in context.

## Delegation

My setup uses a lot of delegation — a product lead spawns a design lead, which spawns an architect, which spawns a coder. Each spawn starts a new context window with none of the parent's file reads.

<div class="vs-block">
  <div class="vs-side good">
    <div class="number">5.5%</div>
    <div class="label">Primary sessions</div>
    <div class="sublabel">38 sessions — me talking to an agent</div>
  </div>
  <div class="vs-side bad">
    <div class="number">81.1%</div>
    <div class="label">Subagent sessions</div>
    <div class="sublabel">10,123 sessions — spawned by other agents</div>
  </div>
</div>

When I talk to an agent directly, there's almost no redundancy. I ask it to read a file, it reads it, we move on. Subagents re-read 81% of the time. Every delegation boundary is a cliff — the orchestrator read the files and knows what matters, but the agent it spawns starts blind. The reviewer reads the same files the coder just edited. The alignment checker reads everything the reviewer read.

In one session, an investigator agent spawned an explorer to go read the codebase. Then, instead of waiting for the results, it went and read the same files itself.

### Where in the chain

The redundancy isn't evenly distributed across agent roles.

<div class="bar-chart">
  <div class="bar-row">
    <div class="bar-label">Coder</div>
    <div class="bar-track"><div class="bar-fill highlight" style="width: 79.2%">79.2%</div></div>
    <div class="bar-value">28,288 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Reviewer</div>
    <div class="bar-track"><div class="bar-fill highlight" style="width: 76.8%">76.8%</div></div>
    <div class="bar-value">31,248 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Explorer</div>
    <div class="bar-track"><div class="bar-fill" style="width: 67.7%">67.7%</div></div>
    <div class="bar-value">13,884 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Architect</div>
    <div class="bar-track"><div class="bar-fill" style="width: 63.6%">63.6%</div></div>
    <div class="bar-value">5,700 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Smoke tester</div>
    <div class="bar-track"><div class="bar-fill muted" style="width: 56.5%">56.5%</div></div>
    <div class="bar-value">6,474 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Investigator</div>
    <div class="bar-track"><div class="bar-fill muted" style="width: 62.5%">62.5%</div></div>
    <div class="bar-value">3,915 reads</div>
  </div>
</div>

Reviewers and coders dominate — they're the leaf nodes, furthest from the original file reads. Reviewers alone account for 31,248 reads, more than any other role, and nearly all of that is re-reading files the coder already touched. Explorers are slightly better because their whole job is reading — they tend to be first to open a file in their session.

### Across harnesses

This isn't specific to one tool.

<div class="bar-chart">
  <div class="bar-row">
    <div class="bar-label">Codex</div>
    <div class="bar-track"><div class="bar-fill highlight" style="width: 83.1%">83.1%</div></div>
    <div class="bar-value">77,940 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Meridian</div>
    <div class="bar-track"><div class="bar-fill" style="width: 79.5%">79.5%</div></div>
    <div class="bar-value">16,963 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">Claude Code</div>
    <div class="bar-track"><div class="bar-fill" style="width: 72.2%">72.2%</div></div>
    <div class="bar-value">49,976 reads</div>
  </div>
  <div class="bar-row">
    <div class="bar-label">OpenCode</div>
    <div class="bar-track"><div class="bar-fill muted" style="width: 50.6%">50.6%</div></div>
    <div class="bar-value">1,133 reads</div>
  </div>
</div>

Codex has the highest redundancy rate because most of its sessions are leaf agents (coders, reviewers) spawned from orchestrators in Claude Code. OpenCode is lowest partly because I used it less for multi-agent work — shorter, more interactive sessions.

## Compaction

When an agent fills its context window, it compacts — summarizing the conversation to free up space. The summary is lossy. The agent loses the contents of files it read, so it reads them again, which fills context again, which triggers another compaction.

<div class="vs-block">
  <div class="vs-side bad">
    <div class="number">$24.56</div>
    <div class="label">Avg cost with compaction</div>
    <div class="sublabel">250 sessions — 69.4 avg reads — 39.4% redundancy</div>
  </div>
  <div class="vs-side good">
    <div class="number">$2.18</div>
    <div class="label">Avg cost without</div>
    <div class="sublabel">4,089 sessions — 12.1 avg reads — 20.5% redundancy</div>
  </div>
</div>

Sessions that compacted cost 11x more on average. The top session compacted 29 times. One coder session compacted 22 times in 12 minutes — roughly every 33 seconds. Read, fill context, compact, forget, re-read.

525 total compaction events across 250 sessions. Every single one was in Claude Code — Codex and OpenCode don't expose compaction events in their logs, so this is a lower bound.

## Cost

Estimating cost across three harnesses and a dozen models is rough, but the token counts are exact. Based on published API pricing:

<div class="stat-hero">
  <div class="number">~$15,100</div>
  <div class="label">estimated over 3 months at API rates</div>
  <div class="detail">on a $200/month plan</div>
</div>

<table class="comparison-table">
  <tr><th>Harness</th><th class="num">Estimated cost</th><th class="num">Sessions</th></tr>
  <tr><td>Claude Code</td><td class="num highlight-cell">$10,288</td><td class="num">3,080</td></tr>
  <tr><td>Codex</td><td class="num">$4,753</td><td class="num">929</td></tr>
  <tr><td>OpenCode</td><td class="num">$27</td><td class="num">219</td></tr>
</table>

My Codex plan is $200/month. That's $600 for the period. Claude Code was the majority of the cost — Opus sessions aren't cheap.

The weekly cost ramped as multi-agent usage scaled:

<div class="cost-ramp" style="margin-bottom: 3rem;">
  <div class="cost-bar" style="height: 1%;">
    <div class="cost-bar-value">$37</div>
    <div class="cost-bar-label">Feb avg</div>
  </div>
  <div class="cost-bar" style="height: 15%;">
    <div class="cost-bar-value">$968</div>
    <div class="cost-bar-label">W11</div>
  </div>
  <div class="cost-bar" style="height: 26%;">
    <div class="cost-bar-value">$1,671</div>
    <div class="cost-bar-label">W15</div>
  </div>
  <div class="cost-bar" style="height: 17%;">
    <div class="cost-bar-value">$1,072</div>
    <div class="cost-bar-label">W16</div>
  </div>
  <div class="cost-bar" style="height: 28%;">
    <div class="cost-bar-value">$1,767</div>
    <div class="cost-bar-label">W17</div>
  </div>
  <div class="cost-bar" style="height: 43%;">
    <div class="cost-bar-value">$2,732</div>
    <div class="cost-bar-label">W18</div>
  </div>
  <div class="cost-bar peak" style="height: 100%;">
    <div class="cost-bar-value">$6,325</div>
    <div class="cost-bar-label">W19</div>
  </div>
</div>

The peak day was May 8: $2,673 across 531 sessions — the large refactor with dozens of agents running in parallel.

A caveat on model comparisons: the per-model redundancy rates range from 32.8% to 89.0%, but this is almost entirely confounded by role. GPT-5.5 was my orchestrator — it delegates instead of reading. GPT-5.4 was the leaf coder. Controlling for role, every model re-reads more than half its files. The model matters less than where it sits in the delegation chain.

<div class="callout">
82% of the cache was hits — roughly 10.4 billion cache read tokens vs 2 billion input tokens. The caching is working. The problem is upstream: the agent shouldn't need to re-read the file at all.
</div>

The providers aren't being stingy. A $200/month plan covering what would cost thousands in API calls is a massive subsidy.

I don't have a fix yet. The script is rerunnable, so I can measure before/after as I try things. I'll report back.

The data and analysis script are on [GitHub](https://github.com/haowjy).

---

*10,163 sessions across Claude Code, Codex, and OpenCode. February–May 2026.*
