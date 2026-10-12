-- Município (IBGE, SP) a que o documento pertence, quando a fonte o informa.
ALTER TABLE documents ADD COLUMN city TEXT;
CREATE INDEX idx_documents_city ON documents(city);
