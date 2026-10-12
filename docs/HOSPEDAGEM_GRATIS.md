# Hospedagem gratuita — passo a passo

## Por que a Oracle Cloud (região São Paulo)

O JurisBusca precisa de quatro coisas que a maioria dos planos gratuitos não oferece:

| Necessidade | Motivo |
|---|---|
| **Servidor no Brasil** | A API do DJEN (despachos e decisões atuais do TJSP e TRT-15) e os portais da Justiça do Trabalho recusam acessos de fora do Brasil. |
| **Disco permanente** | O acervo (banco SQLite + PDFs) precisa sobreviver a reinícios. |
| **Ficar sempre ligado** | O agendador de coletas roda dentro do programa. |
| **Instalar o Tesseract** | Necessário para o OCR de PDFs digitalizados. |

O plano **Always Free** da Oracle Cloud tem servidores na região **Brazil East (São Paulo)**, disco
permanente e máquinas que não "dormem". Render, Railway, Vercel, Hugging Face e similares hospedam
fora do Brasil e/ou apagam o disco/desligam a aplicação quando ociosa — o DJEN não funcionaria e o
acervo se perderia.

> Avisos honestos sobre a Oracle:
> - O cadastro pede **cartão de crédito** para verificação (não há cobrança enquanto você usar só
>   recursos "Always Free").
> - A **região inicial (home region) não pode ser trocada depois**: escolha **Brazil East (São Paulo)**.
> - A Oracle reduziu em 2026 a cota gratuita das máquinas ARM (para 2 OCPUs / 12 GB, segundo
>   reportagens). É muito mais do que o JurisBusca precisa.
> - Às vezes falta capacidade ARM gratuita na região ("Out of capacity"): tente de novo mais tarde
>   ou use a máquina gratuita "VM.Standard.E2.1.Micro" (1 GB de RAM — funciona, mas o OCR fica lento).
> - Contas gratuitas com máquinas muito ociosas podem ter a máquina recuperada pela Oracle; o uso
>   diário evita isso. Faça backup (item 6).

**Alternativa sem servidor:** rodar no seu próprio computador (`./run.sh`). É grátis, está no Brasil e
funciona bem — só fica acessível enquanto o computador estiver ligado.

---

## 1. Criar a conta
1. Acesse **oracle.com/br/cloud/free** e clique em "Comece gratuitamente".
2. Em **Home Region**, escolha **Brazil East (Sao Paulo)**.
3. Conclua a verificação (e-mail, telefone e cartão).

## 2. Criar o servidor
1. No painel: **Compute → Instances → Create instance**.
2. **Image**: Canonical **Ubuntu 24.04**.
3. **Shape**: *Change shape* → **Ampere** → `VM.Standard.A1.Flex` com **1 OCPU e 6 GB** (dentro do gratuito).
   Se aparecer "Out of capacity", tente outro horário ou use `VM.Standard.E2.1.Micro`.
4. **Networking**: mantenha "Assign a public IPv4 address" marcado.
5. **Add SSH keys**: escolha *Generate a key pair for me* e clique em **Save private key**
   (guarde o arquivo `.key`).
6. Clique em **Create** e anote o **Public IP address** (ex.: `150.230.10.20`).

## 3. Abrir as portas 80 e 443
1. Na página da instância: clique na **Subnet** → **Security Lists** → *Default Security List*.
2. **Add Ingress Rules** (duas regras):
   - Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination port **80**
   - Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination port **443**

(O script do passo 5 também libera as portas no firewall interno do Ubuntu.)

## 4. Enviar o JurisBusca para o servidor
No seu computador (Windows: PowerShell; Mac/Linux: Terminal), na pasta onde estão o `.zip` e a chave:

```bash
scp -i ssh-key.key juris-busca.zip ubuntu@SEU_IP:~
ssh -i ssh-key.key ubuntu@SEU_IP
```

> No Mac/Linux, se reclamar das permissões da chave: `chmod 600 ssh-key.key`.
> No Windows, se reclamar: clique com o botão direito na chave → Propriedades → Segurança →
> deixe só o seu usuário com acesso.

## 5. Instalar (dentro do servidor)

```bash
sudo apt-get update && sudo apt-get install -y unzip
unzip juris-busca.zip && cd juris-busca
sudo bash deploy/instalar.sh
```

Ao final aparecem o **endereço** e o **token de acesso**, por exemplo:

```
Pronto! Acesse:  https://150-230-10-20.sslip.io
Token de acesso: Xk3...
```

O endereço `*.sslip.io` é gratuito e aponta para o seu IP; o HTTPS é emitido automaticamente
(Let's Encrypt). Abra o endereço, informe o token quando pedido — e pronto.

Quer um nome mais bonito? Crie um subdomínio grátis no **duckdns.org** apontando para o seu IP e
rode: `sudo DOMINIO=meunome.duckdns.org bash deploy/instalar.sh` (apague `deploy/.env` antes, se já
tiver instalado).

## 6. Depois de instalar

| Tarefa | Comando (na pasta `juris-busca/deploy`) |
|---|---|
| Ver o token | `sudo cat .env` |
| Ver logs | `sudo docker compose logs -f app` |
| Reiniciar | `sudo docker compose restart` |
| Atualizar versão | substitua os arquivos e rode `sudo docker compose up -d --build` |
| Backup | `sudo tar czf ~/backup-juris-$(date +%F).tgz dados` e copie com `scp` para o seu computador |

Primeira coisa a fazer no sistema hospedado: **Fontes → Testar conexão** nas fontes **DJEN — TJSP** e
**DJEN — TRT-15**. Estando no Brasil, elas devem responder; a primeira coleta bem-sucedida muda o
estado para "Ativa". Depois, em **Editar**, defina a coleta agendada (ex.: 1440 min = diária).

## Segurança
- O token é obrigatório no servidor (gerado automaticamente). Não o compartilhe.
- O sistema só acessa os domínios do TJSP, TRT-15/JT e DJEN.
- Para o resumo por IA, adicione `ANTHROPIC_API_KEY=...` em `deploy/.env` e rode
  `sudo docker compose up -d` (isso tem custo na Anthropic; é opcional).

---

## Alternativa: Vercel (somente demonstração)

O repositório já vem pronto para a Vercel (`pyproject.toml`, `vercel.json`). Lá o sistema roda em
**modo temporário**, com um aviso fixo na tela:

- o acervo fica em `/tmp` e é **apagado** quando a função reinicia (e cada instância tem o seu);
- não há coletas agendadas nem OCR;
- cada coleta/consulta online é limitada a poucas páginas (`JURIS_MAX_PAGINAS_TEMPORARIO`, padrão 2)
  e a pesquisa para de iniciar novas consultas após ~180 s (limite de 300 s da Vercel); o que não
  foi consultado aparece no relatório de cobertura como "não consultado", nunca como "sem resultados";
- as funções rodam na região **São Paulo (`gru1`)**, definida em `vercel.json`. O DJEN *pode* aceitar
  o acesso a partir dela — confirme em **Fontes → Testar conexão**.

Passos:
1. Importe o repositório na Vercel (Add New → Project).
2. **Settings → Environment Variables**: crie `JURIS_APP_TOKEN` com uma senha longa
   (sem ela o sistema fica bloqueado, porque o endereço da Vercel é público).
3. Faça um novo deploy (Deployments → Redeploy) e abra o endereço; informe o token quando pedido.
4. (Opcional) Se o DJEN recusar o acesso mesmo na região São Paulo, crie a variável `JURIS_PROXY`
   com um proxy HTTP **seu ou contratado** no Brasil (`http://host:porta` ou
   `http://usuario:senha@host:porta`) e faça novo deploy. Todas as requisições às fontes passam
   por ele; o endereço aparece no topo da tela (sem a senha).
