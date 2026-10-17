# Interview points

Short, honest talking points for Jagadish-text2sql-data-agent. Every number quoted is from the
real run shown in the README.

- Text-to-SQL is mostly a safety and grounding problem; I put a guard between generation and the database.
- Real run: the 15-question gold set scores 1.00 on parse and execution, checked row by row against hand-written SQL. I call that a correctness check because the templates and gold set were written together.
- PII by role: analyst queries for customer email are blocked; admin queries get a LIMIT 1000 cap.
- Clarify, don't guess: 'top products by revenue' asks how many.

## Likely follow-up questions

- How would an LLM change this? (It replaces the parser; the guard and eval stay the same.)
- What about wide schemas? (Retrieve relevant tables first, then generate.)
- Why not trust a read-only DB user alone? (Defence in depth: PII columns and runaway queries need checks too.)
