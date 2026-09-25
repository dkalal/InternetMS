-- Run against the existing production schema BEFORE deploying migration 0032.
-- Read-only: reconstruct the same cart provenance and document ancestry used
-- by billing.0032. Never change an issued balance based on this report.
BEGIN TRANSACTION READ ONLY;

WITH RECURSIVE walk_in_documents (id, tenant_id, customer_id) AS (
    SELECT document.id, document.tenant_id, document.customer_id
    FROM inventory_cart AS cart
    JOIN billing_billingdocument AS document
      ON document.id IN (cart.quotation_id, cart.invoice_id)
     AND document.tenant_id = cart.tenant_id
    WHERE cart.customer_id IS NULL
      AND (cart.quotation_id IS NOT NULL OR cart.invoice_id IS NOT NULL)

    UNION

    SELECT child.id, child.tenant_id, child.customer_id
    FROM billing_billingdocument AS child
    JOIN walk_in_documents AS source
      ON child.tenant_id = source.tenant_id
     AND child.customer_id = source.customer_id
     AND (
         child.parent_quotation_id = source.id
         OR child.source_quotation_id = source.id
         OR child.original_invoice_id = source.id
         OR child.invoice_id = source.id
         OR child.corrected_invoice_id = source.id
     )
),
historical_invoices AS (
    SELECT document.id, document.tenant_id, document.number,
           document.status, document.balance_brought_forward
    FROM billing_billingdocument AS document
    JOIN walk_in_documents AS walk_in ON walk_in.id = document.id
    WHERE document.document_type = 'invoice'
)
SELECT tenant_id,
       COUNT(*) AS identified_walk_in_invoices,
       COUNT(*) FILTER (WHERE balance_brought_forward <> 0) AS invoices_to_review,
       COALESCE(
           jsonb_agg(
               jsonb_build_object(
                   'id', id,
                   'number', number,
                   'status', status,
                   'balance_brought_forward', balance_brought_forward
               ) ORDER BY id
           ) FILTER (WHERE balance_brought_forward <> 0),
           '[]'::jsonb
       ) AS review_references
FROM historical_invoices
GROUP BY tenant_id
ORDER BY tenant_id;

COMMIT;
