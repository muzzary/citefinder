from query import retrieve

question = input("Question to inspect: ")
chunks = retrieve(question, top_k=8)   # pull a few more than usual

print(f"\nRetrieved {len(chunks)} chunks for: '{question}'\n")
for i, c in enumerate(chunks, 1):
    print(f"--- #{i}  (page {c['page']}, {c['source']}) ---")
    print(c["text"][:300].replace("\n", " "))
    print()