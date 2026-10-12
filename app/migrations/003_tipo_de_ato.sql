-- Tipo de ato declarado pela fonte, normalizado (despacho | decisao | sentenca | acordao | outro).
ALTER TABLE documents ADD COLUMN act_kind TEXT;
CREATE INDEX idx_documents_act_kind ON documents(act_kind);
-- Escopo "somente despachos e decisões": fontes que só têm sentenças ou páginas de diário sem tipo
-- declarado deixam de ser consultadas (podem ser reativadas manualmente em Fontes).
UPDATE sources SET enabled = 0, status = 'desativada',
  status_message = 'Desativada: contém apenas sentenças, fora do escopo "somente despachos e decisões".'
  WHERE slug = 'tjsp-cjpg';
UPDATE sources SET enabled = 0, status = 'desativada',
  status_message = 'Desativada: páginas do diário misturam vários atos sem tipo declarado pela fonte; fora do escopo "somente despachos e decisões".'
  WHERE slug = 'tjsp-dje';
