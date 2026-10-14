"""Production reader scope: economic security over generic defence or niche AI."""
BOUNDARY = """
READER SCOPE: economic security is the substantive connection between economic
capability or vulnerability and strategic power. Apply this gate BEFORE assigning
any topic. The article must analyse a concrete economic mechanism: strategic
dependency, supplier/market concentration, resilience of productive capacity or
essential supply, economic leverage, access restrictions, or national technological
and industrial competitiveness. National-security language, a dual-use capability,
the use of AI, and defence relevance alone are insufficient. Do not manufacture
the economic connection in why_it_matters when the body does not analyse it.

DEFENCE: retain substantial analysis of defence-industrial production capacity,
critical inputs, supplier concentration/dependence, industrial policy, technology
transfer or export restrictions. Exclude operational updates, tactics, force
posture, weapons performance, military AI applications and routine logistics when
there is no sustained strategic economic analysis. A need for more weapons alone
does not establish industrial vulnerability; analysis of bottlenecks, domestic
production or concentrated suppliers does. Do not reject a qualifying industrial
article merely because it is predominantly about defence.

AI/TECHNOLOGY: retain analysis of strategic compute and chip access, dependency on
foreign platforms, concentration of technological power, national AI productive
capacity/competitiveness, export controls and industrial policy. Governance
qualifies only when the body materially analyses its effect on these economic
relationships or capabilities. Exclude niche applications, algorithm performance,
individual incidents, narrow safety/biosecurity/ethics questions, generic AI
governance and speculative AI security scenarios without substantial economic
analysis. A potentially large harm or generic economic consequence alone does
not establish this strategic economic mechanism. Apply this gate to all topics:
an AI safety article cannot bypass it through Critical Infrastructure Protection.
Reader-confirmed exclusion: sector-specific AI application/collaboration pieces,
such as life-sciences or agrifood adoption, remain too niche even when framed as
national industrial capability. Distinguish these from strategic semiconductor
supply, national compute access, foreign AI-platform dependence and AI export
controls, which remain in scope. Economic impact alone does not establish relevance.

SECTIONS: central qualifying economic-security analysis belongs in main; sustained
supporting analysis of the SAME qualifying mechanism belongs in further_reading.
Further reading must not retain peripheral defence/AI articles. Passing mentions
or unsupported inference are excluded. Missing/teaser evidence remains needs_review.
The coverage_relationship and topic roles refer ONLY to a qualifying strategic
economic mechanism under this reader scope, NOT to how central AI or defence is
as a general subject. If a niche AI or military article fails this gate, classify
coverage_relationship as none (or passing if a qualifying mechanism is merely
mentioned), set is_material_match=false, editorial_tier=excluded, and leave
matched_topics/topic_evidence/evidence empty. Never return central or
sustained_support for an excluded article. The scope gate takes precedence over
broader examples in the general topic ontology.
"""

TECH_INCLUDE = "Substantial analysis of strategic technology access/dependence, compute or semiconductor supply, national technological/industrial competitiveness, market concentration, or technology controls. AI governance and dual-use technology qualify only with a substantiated strategic economic mechanism."
TECH_EXCLUDE = "Niche AI/technology applications, technical performance, military use, narrow safety/biosecurity/ethics, or generic governance without substantive strategic economic analysis. National-security relevance alone is insufficient."
