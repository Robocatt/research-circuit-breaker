# Импорт библиотек и модулей: Flask – для создания веб-приложения,
# opentelemetry – для реализации распределённого трассирования.
import os, time, flask, opentelemetry
# Настройка точки сбора OTLP и ресурсов сервиса:
# Получаем endpoint сборщика из переменной окружения (с запасным значением),
# создаём ресурс с атрибутом "service.name" для идентификации сервиса,
# устанавливаем провайдер трассировки с заданным ресурсом.
otlp_endpoint = os.getenv("OTLP_COLLECTOR_ENDPOINT", "collector-endpoint:4317")
resource = Resource(attributes={"service.name": "flask-server"})
trace.set_tracer_provider(TracerProvider(resource=resource))
# Инициализация экспортера спанов и добавление процессора:
# Создаём экспортера для отправки спанов через gRPC к OTLP коллектора,
# добавляем процессор спанов для пакетной отправки данных.
otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
# Инициализация Flask приложения и инструментация для трассировки.
# Конфигурация приложения для автоматического отслеживания запросов.
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()
tracer = trace.get_tracer(__name__)
# Определение маршрута для обработки POST-запросов по адресу '/echo':
@app.route('/echo', methods=['POST'])
def echo():
    # Начало корневого спана для всего процесса обработки запроса echo.
    # Этот спан объединяет в себе все внутренние спаны, отражая общий цикл обработки.
    with tracer.start_as_current_span("echo_request_server") as span:
        start_time = time.time()  
        # Спан для разбора входящего JSON-пайлоада:
        # Извлекаем данные из запроса и получаем сообщение.
        with tracer.start_as_current_span("parse_request_server") as parse_span:
            data = request.get_json()
            message = data.get("message", "") if data else ""
            parse_span.set_attribute("message.length", len(message))  
        # Спан для обработки сообщения:
        # Логируем полученное сообщение и фиксируем факт его обработки.
        with tracer.start_as_current_span("process_message_server") as process_span:
            print(f"[Server] Received message from client: {message}")
            # Отмечаем, что сообщение было залогировано.
            process_span.set_attribute("message.logged", True) 
        # Спан для формирования ответа:
        # Создаём словарь с ответом, включающий эхо-сообщение и временную метку,
        # фиксируем размер сформированного ответа и преобразуем словарь в JSON.
        with tracer.start_as_current_span("construct_response_server") as construct_span:
            response_data = {"message": f"Echo: {message}","timestamp": int(time.time())}
            construct_span.set_attribute("response.size", len(str(response_data)))
            response = jsonify(response_data)
        # Добавляем общую длительность обработки запроса в атрибуты корневой спан.
        span.set_attribute("total.duration", time.time() - start_time)
        # Возвращаем ответ клиенту.
        return response
# При запуске скрипта напрямую, запускается Flask сервер на указанном хосте и порте.
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
