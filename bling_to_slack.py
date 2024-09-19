import requests
import os
import threading
import time
import unicodedata
from datetime import datetime, timedelta, time as dt_time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest

# Obtém as variáveis de ambiente
BLING_API_KEY = os.getenv('BLING_API_KEY')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_APP_TOKEN = os.getenv('SLACK_APP_TOKEN')  # Token de nível de aplicativo para o Socket Mode
SLACK_CHANNEL_ID = os.getenv('SLACK_CHANNEL_ID')
SLACK_ACRYLIC_CHANNEL_ID = os.getenv('SLACK_ACRYLIC_CHANNEL_ID')

# Verifica se os tokens e IDs estão definidos
if not BLING_API_KEY:
    print("Erro: BLING_API_KEY não está definido.")
if not SLACK_BOT_TOKEN:
    print("Erro: SLACK_BOT_TOKEN não está definido.")
if not SLACK_APP_TOKEN:
    print("Erro: SLACK_APP_TOKEN não está definido.")
if not SLACK_CHANNEL_ID:
    print("Erro: SLACK_CHANNEL_ID não está definido.")
if not SLACK_ACRYLIC_CHANNEL_ID:
    print("Erro: SLACK_ACRYLIC_CHANNEL_ID não está definido.")

# Inicializa os clientes do Slack
client = WebClient(token=SLACK_BOT_TOKEN)
socket_mode_client = SocketModeClient(app_token=SLACK_APP_TOKEN, web_client=client)

def normalize_str(s):
    if s is None:
        return ''
    s = s.lower()
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')
    return s

def get_sales_orders(start_date=None, end_date=None):
    url = 'https://bling.com.br/Api/v2/pedidos/json/'

    if start_date is None or end_date is None:
        # Obter a data atual no formato dd/mm/aaaa
        today = datetime.today().strftime('%d/%m/%Y')
        start_date = end_date = today
    else:
        # Garantir que as datas estão no formato dd/mm/aaaa
        start_date = start_date.strftime('%d/%m/%Y')
        end_date = end_date.strftime('%d/%m/%Y')

    # Montar o filtro corretamente
    filters = f'dataEmissao[{start_date} TO {end_date}]'

    params = {
        'apikey': BLING_API_KEY,
        'filters': filters
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        retorno = response.json()['retorno']
        if 'pedidos' in retorno:
            orders = retorno['pedidos']
            # Filtrar pedidos com situação "Em aberto"
            orders = [order for order in orders if order['pedido']['situacao'] == 'Em aberto']
            return orders
        else:
            print('Nenhum pedido encontrado no período especificado.')
            return []
    except requests.exceptions.HTTPError as http_err:
        print(f"Erro HTTP ao acessar a API do Bling: {http_err}")
        print('Resposta da API do Bling:', response.text)
        return []
    except Exception as err:
        print(f"Erro ao acessar a API do Bling: {err}")
        return []

def get_store_name(pedido):
    # Obter o ID da loja do pedido
    id_loja = pedido.get('loja', 'N/A')

    # Mapear IDs de lojas para nomes de lojas
    lojas = {
        '204764247': 'Mercado Livre - NETHBIKES',
        '204768359': '(FULL) Mercado Livre - NETHBIKES',
        '204774516': '(FULL) Mercado Livre - NETHSHOP',
        '204774520': 'Mercado Livre - NETHSHOP',
        '204768346': 'Amazon - NETHBIKES (Normal)',
        '204781160': 'Magalu - NETHBIKES',
        '204886217': 'Magalu - NETHSHOP',
        '204767286': '(FBA) Amazon - NETHBIKES',
        '204765146': 'Shopee - NETHBIKES',
        '204848504': 'Shopee - NETHSHOP',
        '204880146': 'Shein - NETHSHOP'
    }

    id_loja_str = str(id_loja)
    loja_nome = lojas.get(id_loja_str, 'Desconhecido')
    return loja_nome

def format_order_message(order):
    pedido = order['pedido']
    loja = get_store_name(pedido)
    numero_pedido = pedido.get('numero', 'N/A')
    comprador = pedido['cliente']['nome']
    itens = pedido['itens']
    valor_total = float(pedido.get('totalvenda', 0.0))
    valor_total_str = f"R$ {valor_total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

    mensagem = f"{loja}\n"
    mensagem += f"Nº Pedido: {numero_pedido}\n"
    mensagem += f"Comprador: {comprador}\n"

    for item in itens:
        sku = item['item'].get('codigo', 'N/A')
        descricao = item['item'].get('descricao', 'N/A')
        quantidade = int(float(item['item']['quantidade']))
        mensagem += f"Item: {sku} | {descricao}\n"
        mensagem += f"Quantidade: {quantidade}\n"

    mensagem += f"Valor total: {valor_total_str}\n"
    mensagem += "___________________________________________________________________"
    return mensagem

def send_message_to_slack(message, channel_id):
    try:
        response = client.chat_postMessage(
            channel=channel_id,
            text=message
        )
        print(f"Mensagem enviada ao Slack no canal {channel_id}")
    except SlackApiError as e:
        print(f"Erro ao enviar mensagem ao Slack: {e.response['error']}")
    except Exception as e:
        print(f"Erro desconhecido ao enviar mensagem ao Slack: {e}")

def load_sent_orders(filename='sent_orders.txt'):
    try:
        with open(filename, 'r') as f:
            sent_orders = set(line.strip() for line in f)
    except FileNotFoundError:
        sent_orders = set()
    return sent_orders

def save_sent_orders(sent_orders, filename='sent_orders.txt'):
    with open(filename, 'w') as f:
        for order_id in sent_orders:
            f.write(f"{order_id}\n")

def is_acrylic_order(order):
    itens = order['pedido']['itens']
    for item in itens:
        descricao = normalize_str(item['item'].get('descricao', ''))
        tags = normalize_str(item['item'].get('descricaoDetalhada', ''))
        if "dubon" in tags or "acrilico" in descricao:
            return True
    return False

def extract_acrylic_items(order):
    loja = get_store_name(order['pedido'])
    items_list = []
    itens = order['pedido']['itens']
    for item in itens:
        descricao = normalize_str(item['item'].get('descricao', ''))
        tags = normalize_str(item['item'].get('descricaoDetalhada', ''))
        if "dubon" in tags or "acrilico" in descricao:
            quantidade = int(float(item['item']['quantidade']))
            descricao_produto = item['item'].get('descricao', 'N/A')
            items_list.append({
                'loja': loja,
                'quantidade': quantidade,
                'descricao': descricao_produto
            })
    return items_list

def generate_acrylic_sales_report():
    # Obter pedidos das últimas 24 horas
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    orders = get_sales_orders(start_date=yesterday, end_date=now)

    if not orders:
        print("Nenhum pedido encontrado nas últimas 24 horas.")
        return None

    # Excluir apenas os "FULL" do Mercado Livre
    excluded_stores = [
        '(FULL) Mercado Livre - NETHBIKES',
        '(FULL) Mercado Livre - NETHSHOP',
    ]

    acrylic_sales = []

    for order in orders:
        pedido = order['pedido']
        loja = get_store_name(pedido)
        if loja in excluded_stores:
            continue
        if is_acrylic_order(order):
            itens = extract_acrylic_items(order)
            acrylic_sales.extend(itens)

    if not acrylic_sales:
        print("Nenhuma venda de acrílicos para relatar nas últimas 24 horas.")
        return None

    # Separar vendas por loja
    mercado_livre_sales = [item for item in acrylic_sales if 'Mercado Livre' in item['loja']]
    shopee_sales = [item for item in acrylic_sales if 'Shopee' in item['loja']]

    report_lines = []
    report_lines.append("*RELATÓRIO DE ACRÍLICOS*")

    if mercado_livre_sales:
        report_lines.append("\n*Vendas de Acrílicos - Mercado Livre*")
        for item in mercado_livre_sales:
            report_lines.append(f"{item['quantidade']} | {item['descricao']}")

    if shopee_sales:
        report_lines.append("\n*Vendas de Acrílicos - Shopee*")
        for item in shopee_sales:
            report_lines.append(f"{item['quantidade']} | {item['descricao']}")

    if report_lines:
        report = "\n".join(report_lines)
        return report
    else:
        print("Nenhuma venda de acrílicos para relatar nas últimas 24 horas.")
        return None

def generate_acrylic_sales_report_for_command():
    # Obter pedidos do dia anterior até agora
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    orders = get_sales_orders(start_date=yesterday, end_date=now)

    if not orders:
        return None

    # Excluir apenas os "FULL" do Mercado Livre
    excluded_stores = [
        '(FULL) Mercado Livre - NETHBIKES',
        '(FULL) Mercado Livre - NETHSHOP',
    ]

    acrylic_sales = []

    for order in orders:
        pedido = order['pedido']
        loja = get_store_name(pedido)
        if loja in excluded_stores:
            continue
        if is_acrylic_order(order):
            itens = extract_acrylic_items(order)
            acrylic_sales.extend(itens)

    if not acrylic_sales:
        return None

    # Separar vendas por loja
    mercado_livre_sales = [item for item in acrylic_sales if 'Mercado Livre' in item['loja']]
    shopee_sales = [item for item in acrylic_sales if 'Shopee' in item['loja']]

    report_lines = []
    report_lines.append("*RELATÓRIO DE ACRÍLICOS*")

    if mercado_livre_sales:
        report_lines.append("\n*Vendas de Acrílicos - Mercado Livre*")
        for item in mercado_livre_sales:
            report_lines.append(f"{item['quantidade']} | {item['descricao']}")

    if shopee_sales:
        report_lines.append("\n*Vendas de Acrílicos - Shopee*")
        for item in shopee_sales:
            report_lines.append(f"{item['quantidade']} | {item['descricao']}")

    if report_lines:
        report = "\n".join(report_lines)
        return report
    else:
        return None

def main():
    sent_orders = load_sent_orders()
    last_report_date = None

    # Testa a autenticação do Slack Bot Token
    try:
        auth_response = client.auth_test()
        bot_user_id = auth_response["user_id"]
        print(f"Autenticação bem-sucedida. ID do Bot: {bot_user_id}")
    except SlackApiError as e:
        print(f"Erro de autenticação do Slack Bot Token: {e.response['error']}")
        return

    # Inicia o Socket Mode Client em uma thread separada
    def start_socket_mode():
        socket_mode_client.connect()

    threading.Thread(target=start_socket_mode).start()

    # Função para manipular eventos
    def handle_events_api(client: SocketModeClient, req: SocketModeRequest):
        if req.type == "events_api":
            event_payload = req.payload.get("event", {})
            event_type = event_payload.get("type")

            if event_type == "app_mention":
                handle_app_mention(event_payload)
            # Reconhece a solicitação
            response = SocketModeResponse(envelope_id=req.envelope_id)
            client.send_socket_mode_response(response)

    # Adiciona a função de manipulação de eventos à lista de listeners
    socket_mode_client.socket_mode_request_listeners.append(handle_events_api)

    def handle_app_mention(event_data):
        text = event_data.get("text", "")
        if "relatório" in text.lower():
            report = generate_acrylic_sales_report_for_command()
            channel_id = event_data["channel"]
            if report:
                send_message_to_slack(report, channel_id)
            else:
                send_message_to_slack("Nenhuma venda de acrílicos para relatar.", channel_id)

    while True:
        now = datetime.now()
        current_time = now.time()
        today_date = now.date()

        # Gerar o relatório às 8h da manhã
        if current_time >= dt_time(8, 0) and (last_report_date != today_date):
            report = generate_acrylic_sales_report()
            if report:
                send_message_to_slack(report, SLACK_ACRYLIC_CHANNEL_ID)
            else:
                print("Nenhuma venda de acrílicos para relatar hoje.")
            last_report_date = today_date

        orders = get_sales_orders()
        if orders:
            for order in orders:
                pedido_id = order['pedido']['numero']
                if pedido_id not in sent_orders:
                    # Processar o pedido
                    message = format_order_message(order)
                    send_message_to_slack(message, SLACK_CHANNEL_ID)
                    sent_orders.add(pedido_id)

                    # Verificar se o pedido contém produtos acrílicos
                    if is_acrylic_order(order):
                        loja = get_store_name(order['pedido'])
                        # Excluir apenas os "FULL" do Mercado Livre
                        excluded_stores = [
                            '(FULL) Mercado Livre - NETHBIKES',
                            '(FULL) Mercado Livre - NETHSHOP',
                        ]
                        if loja not in excluded_stores:
                            send_message_to_slack(message, SLACK_ACRYLIC_CHANNEL_ID)
                else:
                    # Pedido já foi enviado
                    pass
            save_sent_orders(sent_orders)
        else:
            print("Nenhum pedido para enviar ao Slack.")

        # Aguarda 60 segundos antes de repetir
        time.sleep(60)

if __name__ == '__main__':
    main()
