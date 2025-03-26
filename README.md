# Forked from [original repo](https://gitlab.com/skibcsit/thesis-template/)

# Бета версия РСПЗ - thesis-template-2-rspz.pdf 



--------------------------------------------

# Шаблон отчетов НИР и УИР #

## Ссылки на результаты сборки ##

Ссылки заданы относительно корня проекта
и доступны только если сборка завершена:

- **просмотреть**
    - [browse](../-/jobs/artifacts/master/browse/?job=build)
- **скачать**
    - [zip-архив](../-/jobs/artifacts/master/download/?job=build)
    - [задание](../-/jobs/artifacts/master/raw/thesis-template-1-task.pdf?job=build)
    - [РСПЗ](../-/jobs/artifacts/master/raw/thesis-template-2-rspz.pdf?job=build)
    - [ПЗ](../-/jobs/artifacts/master/raw/thesis-template-3-pz.pdf?job=build)

## О шаблоне

Это универсальный LaTeX-шаблон для подготовки пояснительных записок, и их расширенных содержаний, к УИРиКП, НИР, дипломным работам бакалавров и магистерских диссертаций. Разработан на кафедра №22 "Кибернетика" НИЯУ МИФИ и соответствует ГОСТам и стандартам и традициям Кафедры.

* Для компиляции используется XeLaTeX
* Установлен шрифт Times New Roman
* Настроена библиография под ГОСТ
* Настроено форматирование заголовков, в т.ч. для приложений
* Заголовки таблиц и подрисуночные подписи по ГОСТу
* Автоматизирована сборка "полной" версии (РС)ПЗ, включающей:
    * титульный лист
    * лист с подписями (для ВКР)
    * задание
* Имеются примеры использования основным возможностей шаблона

В собранном примере имеются многочисленные указания и полезные советы по подготовке пояснительных записок.

### Титульные листы

ВНИМАНИЕ: Бланки и титульные листы сейчас готовятся в формате docx.
Шаблоны доступны в [общей папке](https://drive.google.com/open?id=1Cj2uL9AxFG5LvBQQhNU19ArThawpkKVw).

## Инструментарий ###

Для выполнения различных задач рекомендуется использование следующего инструментария:

* Компиляция LaTeX документов:
    * [MikTex](https://miktex.org/) (Windows)
    * [TexLive](https://tug.org/texlive/) (для Linux/Windows)
* Редактирование кода и сборка проекта (IDE):
    * [Sublime Text](https://www.sublimetext.com/) + [LaTeXTools](https://latextools.readthedocs.io/en/latest/) + [Sumatra PDF](http://www.sumatrapdfreader.org)
    * [TexnicCenter](http://www.texniccenter.org/) + [Sumatra PDF](https://www.sumatrapdfreader.org)
    * [TexStudio](https://www.texstudio.org/)
    * [Visual Studio Code](https://code.visualstudio.com/) с плагинами
        * LaTeX Workshop
        * LaTeX Utilities
* Поиск и организация подборок литературы:
    * [Google Scholar](https://scholar.google.ru/) и другие специализированные поисковики по научной литературе
    * [Zotero](https://www.zotero.org/) и другие библиографические менеджеры
* Редактирования титульных листов и заданий:
    * Заполнение шаблонов титульных листов и заданий (они в формате docx) и их конвертации в pdf
        * MS Word
        * Google Docs
    * Редактирование PDF
        * вставка подписей и др. — [PDF-XChange](https://www.tracker-software.com/product/pdf-xchange-editor)
        * постраничная разбивка или склеивание — [PDFsam](https://pdfsam.org/)
* GUI для работы с git-репозиториями
    * [SmartGit](https://www.syntevo.com/smartgit/)
    * [Tortoise Git](https://tortoisegit.org/)
    * [Gittyup](https://murmele.github.io/Gittyup/)
