"""
Phase-7 gold question set for retrieval evaluation.

A FIXED set of questions over the eval corpus (fyp_final.pdf, the "Digital
Academic Portal" thesis), each labelled with the page number(s) that genuinely
answer it. Labels were verified by reading the actual chunk text on those pages
(see DEVLOG 2026-06-17 Phase 7) — they are human ground truth, not generated.

Relevance is judged at PAGE granularity: a retrieval "hits" a question if it
returns any chunk whose page_number is in relevant_pages. Page-level is the
honest unit here because (a) we already store page numbers and (b) it matches
what the student cares about — "did it point me to the right page?".

The mix is deliberate: some questions favour keywords (exact terms like
"GitHub", "chatbot"), some favour meaning (paraphrased asks) — so the eval can
show where hybrid and multi-query each earn their keep.
"""

# The corpus this set is written against. The evaluator ingests it under this
# user_id if it isn't already present.
CORPUS_PDF = "data/fyp_final.pdf"
EVAL_USER = "eval_corpus"

GOLD = [
    {"q": "How does the system keep user accounts and data secure?",
     "relevant_pages": [22, 43, 44]},
    {"q": "What method is used to authenticate users when they log in?",
     "relevant_pages": [22, 44]},
    {"q": "How does the chatbot help students?",
     "relevant_pages": [14, 21]},
    {"q": "How are assignments submitted and graded?",
     "relevant_pages": [20, 25]},
    {"q": "What backup and recovery features does the system provide?",
     "relevant_pages": [24]},
    {"q": "How does the application achieve scalability under load?",
     "relevant_pages": [22, 43, 46]},
    {"q": "What software development methodology or life cycle was followed?",
     "relevant_pages": [39, 40]},
    {"q": "What future improvements are planned for the project?",
     "relevant_pages": [67, 68]},
    {"q": "Can the app convert images into text or PDF documents?",
     "relevant_pages": [26]},
    {"q": "What version control tools were used during development?",
     "relevant_pages": [45]},
    {"q": "How is student attendance tracked and marked?",
     "relevant_pages": [56, 61]},
    {"q": "How was the system tested before release?",
     "relevant_pages": [57, 58]},
]

# Off-topic questions the corpus genuinely does NOT answer. Used to tune the
# distance floor: a good floor sits ABOVE the best (smallest) dense distance of
# covered questions and BELOW that of these negatives, so "not covered" can be
# refused structurally — before the LLM — instead of leaning on the LLM layer.
OFF_TOPIC = [
    "What is the boiling point of liquid mercury in kelvin?",
    "Who won the 2018 FIFA World Cup final?",
    "Explain the process of photosynthesis in plants.",
    "What is the capital city of France?",
    "How do you bake a traditional sourdough bread?",
    "What is the chemical formula for table salt?",
]
