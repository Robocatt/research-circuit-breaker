  // 1. Импорт библиотек и модулей
  Импортировать модули:
    - os, time, Flask, jsonify, opentelemetry 

  // 2. Настройка OTLP-сборщика и ресурсов сервиса
  Установить:
    otlp_endpoint ← значение из переменной окружения "OTLP_COLLECTOR_ENDPOINT" 
  Создать ресурс:
    resource ← { "service.name": "flask-server" }
  Инициализировать провайдер трассировки:
    trace.set_tracer_provider(TracerProvider(resource = resource))
  // 3. Инициализация экспортера спанов 
  Создать экспортера:
    otlp_exporter ← OTLPSpanExporter(endpoint = otlp_endpoint, insecure = True)
  Добавить обработчик спанов:
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

  // 4. Инициализация Flask приложения и инструментирование трассировки
  Создать Flask приложение:
    app ← Flask(__name__)
  Настроить приложение для автоматического отслеживания:
    FlaskInstrumentor().instrument_app(app)
    RequestsInstrumentor().instrument()
    tracer ← trace.get_tracer(имя текущего модуля)

  // 5. Определение маршрута для обработки POST-запросов по адресу '/echo'
  Определить маршрут '/echo' с методом POST:
    ФУНКЦИЯ echo():
      // Запуск корневого спана для обработки запроса
      С ОТКРЫТЫМ спаном "echo_request_server":
        start_time ← текущее время

        // Спан для разбора запроса
        С ОТКРЫТЫМ спаном "parse_request_server":
          data ← получить JSON из запроса
          Если data существует, то:
            message ← значение поля "message" из data
          Иначе:
            message ← пустая строка
          Установить атрибут "message.length" равным длине message

        // Спан для обработки сообщения
        С ОТКРЫТЫМ спаном "process_message_server":
          Вывести в консоль сообщение: "[Server] Received message from client: " + message
          Установить атрибут "message.logged" равным True

        // Спан для формирования ответа
        С ОТКРЫТЫМ спаном "construct_response_server":
          response_data ← словарь с:
            "message": "Echo: " + message
            "timestamp": текущее время (в виде целого числа)
          Установить атрибут "response.size" равным размеру response_data
          response ← преобразовать response_data в JSON

        total_duration ← текущее время − start_time
        Установить атрибут "total.duration" в корневом спане равным total_duration
        Вернуть response клиенту
  // 6. Запуск Flask сервера при прямом выполнении скрипта
    app.run(host = "0.0.0.0", port = 5001, debug = False)

