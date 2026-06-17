from query import retrieve


def inspect(question, top_k=8):
    """Print the top retrieved chunks for a question so retrieval quality can
    be eyeballed."""
    chunks = retrieve(question, top_k=top_k)   # pull a few more than usual
    print(f"\nRetrieved {len(chunks)} chunks for: '{question}'\n")
    for i, c in enumerate(chunks, 1):
        print(f"--- #{i}  (page {c['page']}, {c['source']}) ---")
        print(c["text"][:300].replace("\n", " "))
        print()


if __name__ == "__main__":
    inspect(input("Question to inspect: "))
