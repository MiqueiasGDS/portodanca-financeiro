import streamlit as st
import re
import sqlite3
import json
from datetime import datetime
import google.generativeai as genai
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
import io

# ==================== CONFIGURAÇÕES ====================

# Gemini API
genai.configure(api_key="AIzaSyDtDbM0Hg4jbWT9CdzQSCEY_s_1EG5vGg0")
model = genai.GenerativeModel('gemini-2.5-flash')

# Código de acesso (ALTERE AQUI)
CODIGO_ACESSO = "PORTO2026"

# Orçamento do projeto
ORCAMENTO = {
    "Recursos Humanos": 14300.00,
    "Materiais": 1100.00,
    "Serviços": 117100.00,
    "Logística": 19500.00,
    "Despesas Administrativas": 38000.00
}
ORCAMENTO_TOTAL = 190000.00

# ==================== BANCO DE DADOS ====================

def init_db():
    conn = sqlite3.connect('portodanca.db')
    c = conn.cursor()
    
    # Tabela de gastos
    c.execute('''CREATE TABLE IF NOT EXISTS gastos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  data TEXT,
                  descricao TEXT,
                  valor REAL,
                  categoria TEXT,
                  data_registro TEXT,
                  informado_por TEXT,
                  message_id INTEGER)''')
    
    # Tabela para mensagens do Telegram
    c.execute('''CREATE TABLE IF NOT EXISTS mensagens_telegram
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  message_id INTEGER UNIQUE,
                  chat_id INTEGER,
                  user_name TEXT,
                  user_id INTEGER,
                  texto TEXT,
                  data_mensagem TEXT,
                  processado INTEGER DEFAULT 0)''')
    
    # Tabela para controlar última sincronização
    c.execute('''CREATE TABLE IF NOT EXISTS sync_control
                 (id INTEGER PRIMARY KEY,
                  ultima_sync TEXT)''')
    
    # Inicializar sync_control se vazio
    c.execute('SELECT COUNT(*) FROM sync_control')
    if c.fetchone()[0] == 0:
        c.execute('INSERT INTO sync_control (id, ultima_sync) VALUES (1, ?)', 
                 (datetime(2020, 1, 1).isoformat(),))
    
    conn.commit()
    conn.close()

# ==================== PROCESSAMENTO TELEGRAM ====================

def extrair_gastos_telegram(texto, user_name):
    """Extrai valor, quantidade e descrição de mensagens do Telegram"""
    # Padrões comuns:
    # "R$ 500,00 para impressão de 1000 folders"
    # "Paguei 300 reais, 5 unidades de camisetas"
    # "Valor: R$ 1.500,00 - Locação de som"
    
    # Extrair valor
    padrao_valor = r'R\$?\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)|(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*reais?'
    match_valor = re.search(padrao_valor, texto, re.IGNORECASE)
    
    if not match_valor:
        return None
    
    valor_str = match_valor.group(1) or match_valor.group(2)
    valor_str = valor_str.replace('.', '').replace(',', '.')
    valor_unitario = float(valor_str)
    
    # Extrair quantidade (opcional)
    padrao_qtd = r'(\d+)\s*(?:unidade|unid|un|peça|peças|itens?|x)'
    match_qtd = re.search(padrao_qtd, texto, re.IGNORECASE)
    
    quantidade = int(match_qtd.group(1)) if match_qtd else 1
    valor_total = valor_unitario * quantidade
    
    # Descrição é o texto completo
    descricao = texto[:200]  # Limitar tamanho
    
    return {
        'descricao': descricao,
        'valor': valor_total,
        'informado_por': user_name,
        'quantidade': quantidade,
        'valor_unitario': valor_unitario
    }

def categorizar_gastos_telegram(gastos):
    """Categoriza gastos usando Gemini"""
    if not gastos:
        return []
    
    categorias = list(ORCAMENTO.keys())
    
    prompt = f"""Você é um assistente financeiro. Analise cada gasto abaixo e categorize-o em UMA das seguintes categorias:
{', '.join(categorias)}

Gastos para categorizar:
{json.dumps(gastos, ensure_ascii=False, indent=2)}

Responda APENAS com um JSON no formato:
[
  {{"descricao": "...", "valor": 123.45, "categoria": "categoria escolhida", "informado_por": "..."}},
  ...
]

IMPORTANTE: 
- Use EXATAMENTE os nomes das categorias fornecidas
- Retorne APENAS o array JSON, sem texto adicional
- Recursos Humanos: salários, pagamentos a pessoas
- Materiais: compras de itens, equipamentos
- Serviços: contratações de terceiros, aluguéis, locações
- Logística: transporte, alimentação, hospedagem
- Despesas Administrativas: gestão, coordenação
"""
    
    try:
        response = model.generate_content(prompt)
        texto_resposta = response.text.strip()
        texto_resposta = texto_resposta.replace('```json', '').replace('```', '').strip()
        gastos_categorizados = json.loads(texto_resposta)
        return gastos_categorizados
    except Exception as e:
        st.error(f"Erro ao categorizar: {e}")
        return [{'descricao': g['descricao'], 'valor': g['valor'], 
                'categoria': 'Serviços', 'informado_por': g['informado_por']} for g in gastos]

def sincronizar_telegram():
    """Busca novas mensagens do Telegram e processa"""
    conn = sqlite3.connect('portodanca.db')
    c = conn.cursor()
    
    # Pegar última sincronização
    c.execute('SELECT ultima_sync FROM sync_control WHERE id = 1')
    result = c.fetchone()
    ultima_sync = datetime.fromisoformat(result[0]) if result else datetime(2020, 1, 1)
    
    # Buscar mensagens não processadas desde última sync
    c.execute('''SELECT id, message_id, user_name, texto, data_mensagem 
                 FROM mensagens_telegram 
                 WHERE processado = 0 AND data_mensagem > ?
                 ORDER BY data_mensagem''', (ultima_sync.isoformat(),))
    
    mensagens = c.fetchall()
    conn.close()
    
    if not mensagens:
        return []
    
    # Extrair gastos de cada mensagem
    gastos_brutos = []
    for msg_id, message_id, user_name, texto, data_msg in mensagens:
        gasto = extrair_gastos_telegram(texto, user_name)
        if gasto:
            gasto['message_id'] = message_id
            gasto['msg_db_id'] = msg_id
            gasto['data_mensagem'] = data_msg
            gastos_brutos.append(gasto)
    
    if not gastos_brutos:
        return []
    
    # Categorizar com IA
    gastos_categorizados = categorizar_gastos_telegram(gastos_brutos)
    
    # Adicionar informações adicionais
    for i, gasto in enumerate(gastos_categorizados):
        if i < len(gastos_brutos):
            gasto['message_id'] = gastos_brutos[i]['message_id']
            gasto['msg_db_id'] = gastos_brutos[i]['msg_db_id']
            gasto['data_mensagem'] = gastos_brutos[i]['data_mensagem']
    
    return gastos_categorizados

# ==================== OPERAÇÕES DE GASTOS ====================

def salvar_gastos(gastos):
    conn = sqlite3.connect('portodanca.db')
    c = conn.cursor()
    data_registro = datetime.now().isoformat()
    
    for gasto in gastos:
        c.execute('''INSERT INTO gastos (data, descricao, valor, categoria, data_registro, informado_por, message_id)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (gasto.get('data_mensagem', datetime.now().isoformat())[:10], 
                   gasto['descricao'], 
                   gasto['valor'], 
                   gasto['categoria'],
                   data_registro,
                   gasto.get('informado_por', 'N/A'),
                   gasto.get('message_id', None)))
        
        # Marcar mensagem como processada
        if 'msg_db_id' in gasto:
            c.execute('UPDATE mensagens_telegram SET processado = 1 WHERE id = ?', 
                     (gasto['msg_db_id'],))
    
    # Atualizar última sincronização
    c.execute('UPDATE sync_control SET ultima_sync = ? WHERE id = 1', 
             (datetime.now().isoformat(),))
    
    conn.commit()
    conn.close()

def carregar_gastos():
    conn = sqlite3.connect('portodanca.db')
    c = conn.cursor()
    c.execute('''SELECT id, data, descricao, valor, categoria, informado_por, message_id
                 FROM gastos ORDER BY data DESC''')
    gastos = []
    for row in c.fetchall():
        gastos.append({
            'id': row[0],
            'data': row[1],
            'descricao': row[2],
            'valor': row[3],
            'categoria': row[4],
            'informado_por': row[5] or 'N/A',
            'message_id': row[6]
        })
    conn.close()
    return gastos

def deletar_gasto(gasto_id):
    conn = sqlite3.connect('portodanca.db')
    c = conn.cursor()
    c.execute('DELETE FROM gastos WHERE id = ?', (gasto_id,))
    conn.commit()
    conn.close()

def calcular_balanco():
    gastos = carregar_gastos()
    totais_por_categoria = {cat: 0.0 for cat in ORCAMENTO.keys()}
    
    for gasto in gastos:
        if gasto['categoria'] in totais_por_categoria:
            totais_por_categoria[gasto['categoria']] += gasto['valor']
    
    total_gasto = sum(totais_por_categoria.values())
    return totais_por_categoria, total_gasto

# ==================== GERAÇÃO DE PDF ====================

def gerar_pdf_balanco():
    """Gera PDF com balanço detalhado"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                           topMargin=2*cm, bottomMargin=2*cm)
    
    elements = []
    styles = getSampleStyleSheet()
    
    # Título
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#1e3a8a'),
        spaceAfter=30,
        alignment=TA_CENTER
    )
    
    elements.append(Paragraph("🎭 Festival Portodança 2026", title_style))
    elements.append(Paragraph("Relatório Financeiro Detalhado", styles['Heading2']))
    elements.append(Spacer(1, 0.5*cm))
    
    # Data do relatório
    data_relatorio = datetime.now().strftime("%d/%m/%Y às %H:%M")
    elements.append(Paragraph(f"Gerado em: {data_relatorio}", styles['Normal']))
    elements.append(Spacer(1, 1*cm))
    
    # Resumo geral
    totais_por_categoria, total_gasto = calcular_balanco()
    saldo = ORCAMENTO_TOTAL - total_gasto
    percentual = (total_gasto / ORCAMENTO_TOTAL) * 100
    
    resumo_data = [
        ['', 'Valor'],
        ['Orçamento Total', f'R$ {ORCAMENTO_TOTAL:,.2f}'],
        ['Total Gasto', f'R$ {total_gasto:,.2f}'],
        ['Saldo Restante', f'R$ {saldo:,.2f}'],
        ['Percentual Utilizado', f'{percentual:.1f}%']
    ]
    
    resumo_table = Table(resumo_data, colWidths=[12*cm, 5*cm])
    resumo_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    elements.append(resumo_table)
    elements.append(Spacer(1, 1*cm))
    
    # Detalhamento por categoria
    elements.append(Paragraph("Detalhamento por Categoria", styles['Heading2']))
    elements.append(Spacer(1, 0.5*cm))
    
    cat_data = [['Categoria', 'Orçado', 'Gasto', 'Saldo', '%']]
    
    for categoria, orcamento in ORCAMENTO.items():
        gasto_cat = totais_por_categoria[categoria]
        saldo_cat = orcamento - gasto_cat
        perc_cat = (gasto_cat / orcamento) * 100 if orcamento > 0 else 0
        
        cat_data.append([
            categoria,
            f'R$ {orcamento:,.2f}',
            f'R$ {gasto_cat:,.2f}',
            f'R$ {saldo_cat:,.2f}',
            f'{perc_cat:.1f}%'
        ])
    
    cat_table = Table(cat_data, colWidths=[6*cm, 3*cm, 3*cm, 3*cm, 2*cm])
    cat_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    elements.append(cat_table)
    elements.append(Spacer(1, 1*cm))
    
    # Lista de gastos
    elements.append(Paragraph("Registro de Gastos", styles['Heading2']))
    elements.append(Spacer(1, 0.5*cm))
    
    gastos = carregar_gastos()
    
    gastos_data = [['Data', 'Descrição', 'Valor', 'Categoria', 'Informado por']]
    
    for gasto in gastos[:50]:  # Limitar a 50 gastos para não ficar muito grande
        gastos_data.append([
            gasto['data'],
            gasto['descricao'][:30] + '...' if len(gasto['descricao']) > 30 else gasto['descricao'],
            f"R$ {gasto['valor']:.2f}",
            gasto['categoria'][:15],
            gasto['informado_por'][:15]
        ])
    
    if len(gastos) > 50:
        gastos_data.append(['...', f'(+{len(gastos)-50} gastos)', '', '', ''])
    
    gastos_table = Table(gastos_data, colWidths=[2*cm, 6*cm, 2.5*cm, 3.5*cm, 3*cm])
    gastos_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
    ]))
    
    elements.append(gastos_table)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer

# ==================== INTERFACE STREAMLIT ====================

def check_autenticacao():
    """Verifica se usuário está autenticado"""
    if 'autenticado' not in st.session_state:
        st.session_state['autenticado'] = False
    
    return st.session_state['autenticado']

def tela_login():
    """Tela de login com código de acesso"""
    st.title("🎭 Festival Portodança - Controle Financeiro 2026")
    st.markdown("---")
    
    st.subheader("🔒 Acesso Restrito")
    st.info("Digite o código de acesso para continuar")
    
    codigo = st.text_input("Código de Acesso", type="password", key="codigo_login")
    
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col2:
        if st.button("Entrar", type="primary", use_container_width=True):
            if codigo == CODIGO_ACESSO:
                st.session_state['autenticado'] = True
                st.rerun()
            else:
                st.error("❌ Código incorreto!")

def main():
    st.set_page_config(page_title="Portodança - Controle Financeiro", layout="wide")
    
    init_db()
    
    # Verificar autenticação
    if not check_autenticacao():
        tela_login()
        return
    
    # Header com logout
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("🎭 Festival Portodança - Controle Financeiro 2026")
    with col2:
        if st.button("🚪 Sair"):
            st.session_state['autenticado'] = False
            st.rerun()
    
    # Sidebar com menu
    menu = st.sidebar.selectbox("Menu", ["📊 Dashboard", "🔄 Atualizar do Telegram", "📝 Revisar Gastos"])
    
    # Botão de atualização rápida na sidebar
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 Sincronizar Telegram", use_container_width=True):
        with st.spinner("Buscando novas mensagens..."):
            novos_gastos = sincronizar_telegram()
            if novos_gastos:
                st.session_state['gastos_pendentes'] = novos_gastos
                st.sidebar.success(f"✅ {len(novos_gastos)} novos gastos encontrados!")
                st.sidebar.info("Vá para 'Revisar Gastos' para confirmar")
            else:
                st.sidebar.info("Nenhum gasto novo desde última sincronização")
    
    if menu == "📊 Dashboard":
        st.header("Balanço Financeiro")
        
        totais_por_categoria, total_gasto = calcular_balanco()
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.metric("Orçamento Total", f"R$ {ORCAMENTO_TOTAL:,.2f}")
            st.metric("Total Gasto", f"R$ {total_gasto:,.2f}")
            saldo = ORCAMENTO_TOTAL - total_gasto
            st.metric("Saldo Restante", f"R$ {saldo:,.2f}", 
                     delta=f"{(saldo/ORCAMENTO_TOTAL)*100:.1f}%" if ORCAMENTO_TOTAL > 0 else "0%")
        
        with col2:
            percentual_gasto = (total_gasto / ORCAMENTO_TOTAL) * 100 if ORCAMENTO_TOTAL > 0 else 0
            st.progress(min(percentual_gasto / 100, 1.0))
            st.write(f"**{percentual_gasto:.1f}%** do orçamento utilizado")
            
            # Botão de PDF
            st.markdown("---")
            pdf_buffer = gerar_pdf_balanco()
            st.download_button(
                label="📄 Baixar Relatório PDF",
                data=pdf_buffer,
                file_name=f"balanco_portodanca_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        
        st.divider()
        st.subheader("Detalhamento por Categoria")
        
        for categoria, orcamento in ORCAMENTO.items():
            gasto = totais_por_categoria[categoria]
            saldo_cat = orcamento - gasto
            percentual = (gasto / orcamento) * 100 if orcamento > 0 else 0
            
            with st.expander(f"**{categoria}** - R$ {gasto:,.2f} / R$ {orcamento:,.2f}"):
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Orçado", f"R$ {orcamento:,.2f}")
                col_b.metric("Gasto", f"R$ {gasto:,.2f}")
                col_c.metric("Saldo", f"R$ {saldo_cat:,.2f}")
                
                st.progress(min(percentual / 100, 1.0))
                st.caption(f"{percentual:.1f}% utilizado")
    
    elif menu == "🔄 Atualizar do Telegram":
        st.header("Sincronizar com Telegram")
        
        st.info("💡 **Como funciona:** O bot do Telegram monitora mensagens do grupo e extrai automaticamente informações de gastos.")
        
        st.markdown("""
        **Formato das mensagens no Telegram:**
        - "Paguei R$ 500,00 para impressão de folders"
        - "Valor: R$ 1.200,00 - Locação de som, 2 unidades"
        - "Gastei 300 reais com transporte"
        """)
        
        st.divider()
        
        col1, col2 = st.columns(2)
        
        with col1:
            conn = sqlite3.connect('portodanca.db')
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM mensagens_telegram WHERE processado = 0')
            nao_processadas = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM mensagens_telegram')
            total_msgs = c.fetchone()[0]
            conn.close()
            
            st.metric("Mensagens Não Processadas", nao_processadas)
            st.metric("Total de Mensagens", total_msgs)
        
        with col2:
            conn = sqlite3.connect('portodanca.db')
            c = conn.cursor()
            c.execute('SELECT ultima_sync FROM sync_control WHERE id = 1')
            result = c.fetchone()
            conn.close()
            
            if result:
                ultima = datetime.fromisoformat(result[0])
                st.metric("Última Sincronização", ultima.strftime("%d/%m/%Y %H:%M"))
        
        st.divider()
        
        if st.button("🔄 Buscar Novos Gastos", type="primary", use_container_width=True):
            with st.spinner("Processando mensagens do Telegram..."):
                novos_gastos = sincronizar_telegram()
                
                if novos_gastos:
                    st.session_state['gastos_pendentes'] = novos_gastos
                    st.success(f"✅ {len(novos_gastos)} novos gastos encontrados e categorizados!")
                    st.info("📝 Vá para **'Revisar Gastos'** para confirmar antes de salvar")
                else:
                    st.warning("Nenhum gasto novo encontrado desde a última sincronização")
    
    elif menu == "📝 Revisar Gastos":
        st.header("Revisar e Confirmar Gastos")
        
        if 'gastos_pendentes' in st.session_state and st.session_state['gastos_pendentes']:
            gastos = st.session_state['gastos_pendentes']
            
            st.write(f"**{len(gastos)} gastos** para revisar:")
            
            gastos_revisados = []
            
            for i, gasto in enumerate(gastos):
                with st.expander(f"Gasto {i+1}: R$ {gasto['valor']:.2f} - {gasto['descricao'][:50]}..."):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        nova_descricao = st.text_area(
                            "Descrição", 
                            value=gasto['descricao'], 
                            key=f"desc_{i}",
                            height=100
                        )
                        informado_por = st.text_input(
                            "Informado por",
                            value=gasto.get('informado_por', 'N/A'),
                            key=f"info_{i}"
                        )
                    
                    with col2:
                        nova_categoria = st.selectbox(
                            "Categoria",
                            options=list(ORCAMENTO.keys()),
                            index=list(ORCAMENTO.keys()).index(gasto['categoria']),
                            key=f"cat_{i}"
                        )
                        
                        novo_valor = st.number_input(
                            "Valor (R$)",
                            value=float(gasto['valor']),
                            min_value=0.0,
                            step=0.01,
                            key=f"val_{i}"
                        )
                        
                        if 'data_mensagem' in gasto:
                            st.caption(f"📅 Data: {gasto['data_mensagem'][:10]}")
                    
                    gastos_revisados.append({
                        'descricao': nova_descricao,
                        'valor': novo_valor,
                        'categoria': nova_categoria,
                        'informado_por': informado_por,
                        'message_id': gasto.get('message_id'),
                        'msg_db_id': gasto.get('msg_db_id'),
                        'data_mensagem': gasto.get('data_mensagem', datetime.now().isoformat())
                    })
            
            st.divider()
            
            col_a, col_b = st.columns(2)
            
            with col_a:
                if st.button("✅ Salvar Todos os Gastos", type="primary", use_container_width=True):
                    salvar_gastos(gastos_revisados)
                    st.session_state['gastos_pendentes'] = []
                    st.success("✅ Gastos salvos com sucesso!")
                    st.rerun()
            
            with col_b:
                if st.button("❌ Cancelar", use_container_width=True):
                    st.session_state['gastos_pendentes'] = []
                    st.rerun()
        
        else:
            st.info("Nenhum gasto pendente de revisão. Use **'Atualizar do Telegram'** para buscar novos gastos")
            
            # Mostrar gastos já salvos
            gastos_salvos = carregar_gastos()
            
            if gastos_salvos:
                st.divider()
                st.subheader("Gastos Registrados")
                
                for gasto in gastos_salvos:
                    col1, col2, col3, col4, col5, col6 = st.columns([1.5, 3, 1.5, 2, 2, 0.7])
                    
                    with col1:
                        st.write(gasto['data'])
                    with col2:
                        st.write(gasto['descricao'][:40] + "...")
                    with col3:
                        st.write(f"R$ {gasto['valor']:.2f}")
                    with col4:
                        st.write(gasto['categoria'][:20])
                    with col5:
                        st.write(gasto['informado_por'][:15])
                    with col6:
                        if st.button("🗑️", key=f"del_{gasto['id']}"):
                            deletar_gasto(gasto['id'])
                            st.rerun()

if __name__ == "__main__":
    main()
