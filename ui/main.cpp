#include <QtWidgets>
#include <QtCore>
#include <QtGui>
#include <algorithm>
#include <functional>
#include <cmath>
#include <memory>

namespace {
QString normalized(QString s) {
    s = s.normalized(QString::NormalizationForm_D).toLower();
    QString out;
    for (const auto c : s) {
        if (c.category() != QChar::Mark_NonSpacing && c.category() != QChar::Mark_SpacingCombining && c.category() != QChar::Mark_Enclosing) out += c;
    }
    return out;
}
QString textId(const QJsonObject &b) {
    const auto id = b.value("id");
    return id.isString() ? id.toString() : QString::number(id.toVariant().toLongLong());
}
QString sizeText(qint64 n) {
    return n >= 1048576 ? QString::number(double(n) / 1048576.0, 'f', 1) + " MB" : QString::number(qMax<qint64>(1, n / 1024)) + " KB";
}
QColor bookColor(const QString &category) {
    static const QList<QColor> colors = {QColor("#d4c7a7"), QColor("#afc4b2"), QColor("#b9c4ce"), QColor("#cfb4a3"), QColor("#c8bfcd"), QColor("#c6cca7")};
    uint hash = 0;
    for (const auto c : category) hash = hash * 31 + c.unicode();
    return colors[int(hash % uint(colors.size()))];
}
QIcon lineIcon(const QString &name, const QColor &color = QColor("#5a6c56"), int size = 22) {
    QPixmap pix(size, size); pix.fill(Qt::transparent);
    QPainter p(&pix); p.setRenderHint(QPainter::Antialiasing);
    p.scale(size / 24.0, size / 24.0);
    p.setPen(QPen(color, 1.6, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    if (name == "search") { p.drawEllipse(QRectF(4, 3, 13, 13)); p.drawLine(QPointF(15, 15), QPointF(21, 21)); }
    else if (name == "folder") { QPainterPath path; path.moveTo(3, 7); path.lineTo(10, 7); path.lineTo(12, 10); path.lineTo(21, 10); path.lineTo(19, 20); path.lineTo(3, 20); path.closeSubpath(); p.drawPath(path); p.drawLine(3, 7, 3, 4); p.drawLine(3, 4, 10, 4); p.drawLine(10, 4, 13, 7); }
    else if (name == "scan") { p.drawLine(3, 9, 3, 3); p.drawLine(3, 3, 9, 3); p.drawLine(15, 3, 21, 3); p.drawLine(21, 3, 21, 9); p.drawLine(21, 15, 21, 21); p.drawLine(21, 21, 15, 21); p.drawLine(9, 21, 3, 21); p.drawLine(3, 21, 3, 15); p.drawLine(7, 12, 17, 12); }
    else if (name == "export") { p.drawLine(12, 3, 12, 15); p.drawLine(7, 10, 12, 15); p.drawLine(12, 15, 17, 10); p.drawLine(4, 16, 4, 21); p.drawLine(4, 21, 20, 21); p.drawLine(20, 21, 20, 16); }
    else if (name == "copy") { p.drawRoundedRect(QRectF(8, 8, 12, 13), 2, 2); p.drawLine(4, 16, 4, 3); p.drawLine(4, 3, 16, 3); p.drawLine(16, 3, 16, 5); }
    else if (name == "book") { p.drawRoundedRect(QRectF(5, 3, 15, 18), 2, 2); p.drawLine(9, 3, 9, 21); p.drawLine(12, 8, 17, 8); p.drawLine(12, 12, 17, 12); }
    else if (name == "log") { p.drawRoundedRect(QRectF(5, 3, 14, 18), 2, 2); p.drawLine(9, 8, 15, 8); p.drawLine(9, 12, 15, 12); p.drawLine(9, 16, 13, 16); }
    else if (name == "right") { p.drawLine(9, 5, 16, 12); p.drawLine(16, 12, 9, 19); }
    else if (name == "left") { p.drawLine(15, 5, 8, 12); p.drawLine(8, 12, 15, 19); }
    else if (name == "dots") { p.setBrush(color); p.setPen(Qt::NoPen); p.drawEllipse(QPointF(5, 12), 1.6, 1.6); p.drawEllipse(QPointF(12, 12), 1.6, 1.6); p.drawEllipse(QPointF(19, 12), 1.6, 1.6); }
    else if (name == "check") { p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawLine(7, 12, 11, 16); p.drawLine(11, 16, 17, 8); }
    else if (name == "alert") { QPainterPath path; path.moveTo(12, 3); path.lineTo(22, 21); path.lineTo(2, 21); path.closeSubpath(); p.drawPath(path); p.drawLine(12, 9, 12, 14); p.drawPoint(12, 17); }
    else if (name == "close") { p.drawLine(6, 6, 18, 18); p.drawLine(18, 6, 6, 18); }
    return QIcon(pix);
}
QPixmap logoPixmap(int n) {
    QPixmap pix(n, n); pix.fill(Qt::transparent);
    QPainter p(&pix); p.setRenderHint(QPainter::Antialiasing); p.scale(n / 64.0, n / 64.0);
    p.setPen(Qt::NoPen); p.setBrush(QColor("#527451")); p.drawRoundedRect(QRectF(0, 0, 64, 64), 15, 15);
    QPainterPath a; a.moveTo(14, 47); a.lineTo(28, 15); a.lineTo(36, 15); a.lineTo(50, 47); a.lineTo(41, 47); a.lineTo(32, 25); a.lineTo(23, 47); a.closeSubpath();
    p.setBrush(QColor("#f5f5eb")); p.drawPath(a); p.setPen(QPen(QColor("#c3dbb4"), 4)); p.drawLine(23, 40, 41, 40);
    p.setPen(Qt::NoPen); p.setBrush(QColor("#ddbf82")); p.drawEllipse(QPointF(48, 15), 3, 3);
    return pix;
}
void refreshStyle(QWidget *w) { w->style()->unpolish(w); w->style()->polish(w); }
QLabel *label(const QString &text, const char *name, QWidget *parent = nullptr) {
    auto *l = new QLabel(text, parent); l->setTextFormat(Qt::PlainText); l->setObjectName(name); return l;
}
void clearLayout(QLayout *layout) {
    while (auto *item = layout->takeAt(0)) {
        if (item->widget()) { item->widget()->hide(); item->widget()->deleteLater(); }
        if (item->layout()) clearLayout(item->layout());
        delete item;
    }
}
}

class BookCover : public QWidget {
    QJsonObject book;
    QPixmap thumb;
public:
    explicit BookCover(const QJsonObject &b, QWidget *parent = nullptr) : QWidget(parent), book(b) {
        setFixedHeight(163);
        const auto path = b.value("thumbnail").toString();
        if (!path.isEmpty() && !QPixmapCache::find(path, &thumb)) {
            thumb.load(path);
            if (!thumb.isNull()) QPixmapCache::insert(path, thumb);
        }
    }
protected:
    void paintEvent(QPaintEvent *) override {
        QPainter p(this); p.setRenderHint(QPainter::Antialiasing);
        const QRectF r(0, 0, width(), height());
        QPainterPath clip; clip.addRoundedRect(r, 6, 6); p.setClipPath(clip);
        const auto color = bookColor(book.value("category").toString());
        p.fillRect(r, color.lighter(115));
        if (!thumb.isNull()) {
            const auto target = thumb.size().scaled(QSize(width() - 24, height() - 12), Qt::KeepAspectRatio);
            const QRectF cover((width() - target.width()) / 2.0, 6, target.width(), target.height());
            p.fillRect(cover.translated(3, 3), QColor(0, 0, 0, 18));
            p.drawPixmap(cover.toRect(), thumb);
            return;
        }
        p.fillRect(QRectF(15, 0, width() - 31, height()), color);
        p.fillRect(QRectF(15, 0, 7, height()), color.darker(112));
        p.setPen(QPen(QColor(255, 255, 248, 80), 1));
        p.drawEllipse(QRectF(width() - 95, 90, 115, 115));
        p.drawEllipse(QRectF(width() - 76, 105, 78, 78));
        p.drawLine(QPointF(width() - 128, 158), QPointF(width() - 25, 82));
        p.setPen(color.darker(195));
        QFont small("DejaVu Sans", 7); small.setLetterSpacing(QFont::AbsoluteSpacing, 1.5); p.setFont(small);
        p.drawText(QRectF(31, 19, width() - 62, 18), Qt::AlignLeft, "BIBLIOTECA  /  PDF");
        QFont title("DejaVu Serif", 13); title.setWeight(QFont::Medium); p.setFont(title);
        p.drawText(QRectF(31, 47, width() - 65, 77), Qt::AlignLeft | Qt::AlignTop | Qt::TextWordWrap, book.value("title").toString());
        p.setFont(QFont("DejaVu Sans", 7));
        const auto cat = book.value("category").toString();
        p.drawText(QRectF(31, 135, width() - 62, 18), Qt::AlignLeft, p.fontMetrics().elidedText(cat.toUpper(), Qt::ElideRight, width() - 62));
    }
};

class BookCard : public QFrame {
public:
    QString id;
    std::function<void()> clicked;
    explicit BookCard(const QJsonObject &book, bool selected, QWidget *parent = nullptr) : QFrame(parent), id(textId(book)) {
        setObjectName("bookCard"); setProperty("selected", selected); setCursor(Qt::PointingHandCursor);
        setFixedHeight(286); setMinimumWidth(180); setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        auto *box = new QVBoxLayout(this); box->setContentsMargins(11, 11, 11, 11); box->setSpacing(7);
        box->addWidget(new BookCover(book, this));
        auto *title = label(book.value("title").toString(), "bookTitle", this);
        title->setWordWrap(true); title->setFixedHeight(38); title->setAlignment(Qt::AlignTop);
        title->setToolTip(book.value("title").toString()); box->addWidget(title);
        auto *row = new QHBoxLayout; row->setSpacing(5);
        const bool review = book.value("status").toString() != "indexed" || book.value("category").toString() == "Revisar";
        auto *pill = label(book.value("category").toString(), review ? "reviewPill" : "categoryPill", this);
        pill->setMaximumWidth(190); pill->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Fixed);
        pill->setText(pill->fontMetrics().elidedText(pill->text(), Qt::ElideRight, 175));
        row->addWidget(pill); row->addStretch(); box->addLayout(row);
        QString meta = book.value("pages").toInt() > 0 ? QString::number(book.value("pages").toInt()) + " páginas" : "PDF";
        meta += "   ·   " + sizeText(book.value("size").toVariant().toLongLong());
        if (book.value("missing").toBool()) meta = "Arquivo não encontrado";
        box->addWidget(label(meta, "bookMeta", this));
        setToolTip(book.value("path").toString());
        for (auto *w : findChildren<QWidget *>()) w->setAttribute(Qt::WA_TransparentForMouseEvents);
    }
protected:
    void mouseReleaseEvent(QMouseEvent *e) override {
        if (e->button() == Qt::LeftButton && rect().contains(e->pos()) && clicked) clicked();
        QFrame::mouseReleaseEvent(e);
    }
};

class AtlasWindow : public QMainWindow {
    QString resources, dataDir, python, screenshotFile;
    bool smoke = false, demo = false, screenshotScheduled = false, closingAfterScan = false;
    QJsonArray books;
    QList<QJsonObject> filtered;
    QStringList categories;
    QString filter = "__all", selectedId, lastLog;
    int page = 0, columns = 0;
    static constexpr int PageSize = 60;
    QWidget *sidebar = nullptr, *detail = nullptr, *gridContainer = nullptr, *progressFrame = nullptr;
    QVBoxLayout *navigation = nullptr, *detailLayout = nullptr;
    QGridLayout *grid = nullptr;
    QScrollArea *bookScroll = nullptr;
    QLineEdit *search = nullptr;
    QComboBox *sort = nullptr;
    QLabel *statTotal = nullptr, *statCategorized = nullptr, *statReview = nullptr, *statMissing = nullptr;
    QLabel *countLabel = nullptr, *sectionTitle = nullptr, *pageLabel = nullptr, *statusLabel = nullptr, *progressLabel = nullptr;
    QPushButton *scanButton = nullptr, *folderButton = nullptr, *cancelButton = nullptr;
    QToolButton *scanMenuButton = nullptr, *prevButton = nullptr, *nextButton = nullptr;
    QProgressBar *progress = nullptr;
    QProcess *scanProcess = nullptr;
    QList<BookCard *> cards;
    QString pendingScanError;

public:
    AtlasWindow(const QString &r, const QString &d, const QString &capture, bool smokeMode, bool demoMode)
        : resources(r), dataDir(d), screenshotFile(capture), smoke(smokeMode), demo(demoMode) {
        setWindowTitle("Atlas PDF — Biblioteca de matemática"); setWindowIcon(QIcon(logoPixmap(128)));
        resize(1440, 950); setMinimumSize(960, 650);
        python = qEnvironmentVariable("ATLAS_PYTHON", "python3");
        QDir().mkpath(dataDir);
        makeUi();
        auto *refresh = new QShortcut(QKeySequence::Refresh, this);
        connect(refresh, &QShortcut::activated, this, [this] { loadCatalog(); });
        auto *find = new QShortcut(QKeySequence::Find, this);
        connect(find, &QShortcut::activated, this, [this] { search->setFocus(); search->selectAll(); });
        QTimer::singleShot(0, this, [this] {
            if (demo) { makeDemo(); return; }
            runCommand({"categories"}, [this](const QList<QJsonObject> &events, bool ok, const QString &) {
                if (!ok) return;
                for (const auto &e : events) if (e.value("type") == "categories") {
                    categories.clear(); for (const auto c : e.value("categories").toArray()) categories << c.toString();
                    rebuildNavigation(); if (!selectedId.isEmpty()) showDetail(selectedBook());
                }
            });
            loadCatalog();
        });
    }

protected:
    void resizeEvent(QResizeEvent *e) override {
        QMainWindow::resizeEvent(e);
        QTimer::singleShot(80, this, [this] {
            if (bookScroll && qMax(1, qMin(4, (bookScroll->viewport()->width() - 12) / 218)) != columns) renderBooks();
        });
    }
    void closeEvent(QCloseEvent *e) override {
        if (scanProcess && scanProcess->state() != QProcess::NotRunning) {
            e->ignore();
            closingAfterScan = true;
            cancelScan();
            return;
        }
        QMainWindow::closeEvent(e);
    }

private:
    using CommandResult = std::function<void(const QList<QJsonObject> &, bool, const QString &)>;
    void runCommand(const QStringList &args, CommandResult done) {
        auto *proc = new QProcess(this);
        auto output = std::make_shared<QByteArray>();
        auto errors = std::make_shared<QByteArray>();
        auto handled = std::make_shared<bool>(false);
        connect(proc, &QProcess::readyReadStandardOutput, this, [proc, output] { output->append(proc->readAllStandardOutput()); });
        connect(proc, &QProcess::readyReadStandardError, this, [proc, errors] { errors->append(proc->readAllStandardError()); if (errors->size() > 16000) *errors = errors->right(16000); });
        connect(proc, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, [this, proc, output, errors, handled, done](int code, QProcess::ExitStatus exit) {
            if (*handled) return;
            *handled = true;
            output->append(proc->readAllStandardOutput()); errors->append(proc->readAllStandardError());
            QList<QJsonObject> events;
            for (const auto &line : output->split('\n')) {
                QJsonParseError parse; const auto doc = QJsonDocument::fromJson(line, &parse);
                if (parse.error == QJsonParseError::NoError && doc.isObject()) events << doc.object();
            }
            done(events, code == 0 && exit == QProcess::NormalExit, QString::fromUtf8(*errors).trimmed());
            proc->deleteLater();
        });
        connect(proc, &QProcess::errorOccurred, this, [proc, handled, done](QProcess::ProcessError error) {
            if (error == QProcess::FailedToStart && !*handled) {
                *handled = true; done({}, false, proc->errorString()); proc->deleteLater();
            }
        });
        QStringList command{resources + "/backend/indexer.py", "--data-dir", dataDir}; command << args;
        proc->setProgram(python); proc->setArguments(command); proc->start();
    }
    void makeUi() {
        auto *central = new QWidget(this); central->setObjectName("main"); setCentralWidget(central);
        auto *root = new QHBoxLayout(central); root->setContentsMargins(0, 0, 0, 0); root->setSpacing(0);
        sidebar = new QWidget(central); sidebar->setObjectName("sidebar"); sidebar->setFixedWidth(218);
        auto *side = new QVBoxLayout(sidebar); side->setContentsMargins(20, 28, 20, 20); side->setSpacing(7);
        auto *brandRow = new QHBoxLayout; brandRow->setSpacing(10);
        auto *logo = new QLabel(sidebar); logo->setPixmap(logoPixmap(41)); logo->setFixedSize(41, 41);
        brandRow->addWidget(logo); auto *brandText = new QVBoxLayout; brandText->setSpacing(0);
        brandText->addWidget(label("atlas", "brand", sidebar)); brandText->addWidget(label("PDF LIBRARY", "brandSubtitle", sidebar));
        brandRow->addLayout(brandText); brandRow->addStretch(); side->addLayout(brandRow); side->addSpacing(29);
        auto *navScroll = new QScrollArea(sidebar); navScroll->setWidgetResizable(true); navScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        auto *navWidget = new QWidget(navScroll); navWidget->setObjectName("sidebar");
        navigation = new QVBoxLayout(navWidget); navigation->setContentsMargins(0, 0, 0, 0); navigation->setSpacing(4);
        navScroll->setWidget(navWidget); side->addWidget(navScroll, 1);
        auto *rule = new QFrame(sidebar); rule->setObjectName("sideRule"); rule->setFixedHeight(1); side->addWidget(rule); side->addSpacing(12);
        auto *footer = label("CATÁLOGO NO SEU COMPUTADOR", "sideHeading", sidebar); side->addWidget(footer); side->addSpacing(5);
        auto *footerText = label("Seus PDFs, seus caminhos.\nOrganização que fica com você.", "sideFooter", sidebar); footerText->setWordWrap(true); side->addWidget(footerText);
        side->addSpacing(10); auto *local = label("●  Classificação local", "sideFooter", sidebar); side->addWidget(local);
        root->addWidget(sidebar);

        auto *main = new QWidget(central); auto *mainLayout = new QVBoxLayout(main); mainLayout->setContentsMargins(0, 0, 0, 0); mainLayout->setSpacing(0);
        auto *top = new QWidget(main); auto *topLayout = new QVBoxLayout(top); topLayout->setContentsMargins(28, 27, 28, 17); topLayout->setSpacing(9);
        auto *eyebrow = new QHBoxLayout; eyebrow->addWidget(label("CONHECIMENTO EM SEU LUGAR", "eyebrow", top)); eyebrow->addStretch();
        auto *logs = new QPushButton(lineIcon("log"), "Ver logs", top); logs->setObjectName("quiet"); connect(logs, &QPushButton::clicked, this, [this] { showLogs(); }); eyebrow->addWidget(logs);
        auto *exportButton = new QPushButton(lineIcon("export"), "Exportar TXT", top); exportButton->setObjectName("quiet"); connect(exportButton, &QPushButton::clicked, this, [this] { exportCatalog(); }); eyebrow->addWidget(exportButton);
        topLayout->addLayout(eyebrow); topLayout->addWidget(label("Sua biblioteca, organizada.", "heading", top));
        topLayout->addWidget(label("Encontre seus PDFs. Descubra a matemática em cada página.", "subtitle", top)); topLayout->addSpacing(9);
        auto *actions = new QHBoxLayout; actions->setSpacing(8);
        scanButton = new QPushButton(lineIcon("scan", Qt::white), "Buscar PDFs", top); scanButton->setObjectName("primary");
        scanButton->setToolTip("Buscar em Downloads, Área de Trabalho, Documentos, Imagens, Vídeos e outras pastas pessoais.");
        connect(scanButton, &QPushButton::clicked, this, [this] { startScan({}); }); actions->addWidget(scanButton);
        folderButton = new QPushButton(lineIcon("folder"), "Adicionar pasta", top);
        connect(folderButton, &QPushButton::clicked, this, [this] {
            const auto dir = QFileDialog::getExistingDirectory(this, "Escolher pasta para buscar PDFs", QDir::homePath());
            if (!dir.isEmpty()) startScan({"--root", dir});
        }); actions->addWidget(folderButton);
        scanMenuButton = new QToolButton(top); scanMenuButton->setIcon(lineIcon("dots")); scanMenuButton->setToolTip("Mais opções de busca");
        auto *scanMenu = new QMenu(scanMenuButton);
        auto *homeAction = scanMenu->addAction("Buscar em toda a pasta pessoal"); connect(homeAction, &QAction::triggered, this, [this] { startScan({"--all-home"}); });
        auto *computerAction = scanMenu->addAction("Buscar no computador"); computerAction->setToolTip("Percorre os locais acessíveis; pastas de sistema e arquivos virtuais são ignorados.");
        connect(computerAction, &QAction::triggered, this, [this] { startScan({"--computer"}); });
        scanMenu->addSeparator(); auto *reindex = scanMenu->addAction("Ler e classificar os PDFs novamente"); connect(reindex, &QAction::triggered, this, [this] { startScan({"--force"}); });
        scanMenuButton->setMenu(scanMenu); scanMenuButton->setPopupMode(QToolButton::InstantPopup); actions->addWidget(scanMenuButton); actions->addStretch();
        topLayout->addLayout(actions); mainLayout->addWidget(top);

        auto *bodyRow = new QHBoxLayout; bodyRow->setContentsMargins(0, 0, 0, 0); bodyRow->setSpacing(0);
        auto *collection = new QWidget(main); auto *collectionLayout = new QVBoxLayout(collection); collectionLayout->setContentsMargins(28, 10, 24, 12); collectionLayout->setSpacing(17);
        auto *stats = new QHBoxLayout; stats->setSpacing(10);
        addStat(stats, "PDFs na biblioteca", statTotal); addStat(stats, "Catalogados", statCategorized); addStat(stats, "Para revisar", statReview); addStat(stats, "Não encontrados", statMissing);
        collectionLayout->addLayout(stats);
        auto *searchRow = new QHBoxLayout; searchRow->setSpacing(10);
        auto *searchFrame = new QFrame(collection); searchFrame->setObjectName("searchFrame");
        auto *searchLayout = new QHBoxLayout(searchFrame); searchLayout->setContentsMargins(12, 0, 9, 0); searchLayout->setSpacing(5);
        auto *searchIcon = new QLabel(searchFrame); searchIcon->setPixmap(lineIcon("search").pixmap(18, 18)); searchLayout->addWidget(searchIcon);
        search = new QLineEdit(searchFrame); search->setObjectName("search"); search->setPlaceholderText("Buscar por título, palavra ou caminho…"); search->setClearButtonEnabled(true);
        connect(search, &QLineEdit::textChanged, this, [this] { applyFilter(); }); searchLayout->addWidget(search, 1); searchRow->addWidget(searchFrame, 1);
        sort = new QComboBox(collection); sort->addItems({"Título A–Z", "Área da matemática", "Mais recentes"}); sort->setMinimumWidth(116); sort->setMaximumWidth(160);
        connect(sort, qOverload<int>(&QComboBox::currentIndexChanged), this, [this](int) { applyFilter(); }); searchRow->addWidget(sort); collectionLayout->addLayout(searchRow);
        auto *section = new QHBoxLayout; sectionTitle = label("Todos os livros", "sectionTitle", collection); countLabel = label("0 PDFs", "count", collection);
        section->addWidget(sectionTitle); section->addStretch(); section->addWidget(countLabel); collectionLayout->addLayout(section);
        bookScroll = new QScrollArea(collection); bookScroll->setWidgetResizable(true); bookScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        gridContainer = new QWidget(bookScroll); grid = new QGridLayout(gridContainer); grid->setContentsMargins(0, 0, 7, 0); grid->setHorizontalSpacing(15); grid->setVerticalSpacing(16); grid->setAlignment(Qt::AlignTop);
        bookScroll->setWidget(gridContainer); collectionLayout->addWidget(bookScroll, 1);
        auto *paging = new QHBoxLayout; pageLabel = label("", "count", collection); paging->addWidget(pageLabel); paging->addStretch();
        prevButton = new QToolButton(collection); prevButton->setIcon(lineIcon("left")); prevButton->setToolTip("Página anterior");
        nextButton = new QToolButton(collection); nextButton->setIcon(lineIcon("right")); nextButton->setToolTip("Próxima página");
        connect(prevButton, &QToolButton::clicked, this, [this] { if (page > 0) { --page; renderBooks(); bookScroll->verticalScrollBar()->setValue(0); } });
        connect(nextButton, &QToolButton::clicked, this, [this] { if ((page + 1) * PageSize < filtered.size()) { ++page; renderBooks(); bookScroll->verticalScrollBar()->setValue(0); } });
        paging->addWidget(prevButton); paging->addWidget(nextButton); collectionLayout->addLayout(paging);
        bodyRow->addWidget(collection, 1);
        detail = new QWidget(main); detail->setObjectName("detail"); detail->setFixedWidth(290);
        auto *detailOuter = new QVBoxLayout(detail); detailOuter->setContentsMargins(0, 0, 0, 0);
        auto *detailScroll = new QScrollArea(detail); detailScroll->setWidgetResizable(true); detailScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        auto *detailContents = new QWidget(detailScroll); detailLayout = new QVBoxLayout(detailContents); detailLayout->setContentsMargins(22, 24, 22, 22); detailLayout->setSpacing(12);
        detailScroll->setWidget(detailContents); detailOuter->addWidget(detailScroll); bodyRow->addWidget(detail); mainLayout->addLayout(bodyRow, 1);
        progressFrame = new QFrame(main); progressFrame->setObjectName("progressFrame");
        auto *progressBox = new QVBoxLayout(progressFrame); progressBox->setContentsMargins(27, 10, 25, 10); progressBox->setSpacing(7);
        auto *progressRow = new QHBoxLayout; progressLabel = label("", "progressText", progressFrame); progressLabel->setMinimumWidth(0); progressLabel->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
        progressRow->addWidget(progressLabel, 1); cancelButton = new QPushButton("Cancelar", progressFrame); cancelButton->setObjectName("danger");
        connect(cancelButton, &QPushButton::clicked, this, [this] { cancelScan(); }); progressRow->addWidget(cancelButton); progressBox->addLayout(progressRow);
        progress = new QProgressBar(progressFrame); progress->setTextVisible(false); progressBox->addWidget(progress); mainLayout->addWidget(progressFrame); progressFrame->hide();
        statusLabel = label("Pronto para encontrar seus PDFs.", "count", main); statusLabel->setContentsMargins(28, 6, 20, 10); statusLabel->setTextInteractionFlags(Qt::TextSelectableByMouse); mainLayout->addWidget(statusLabel);
        root->addWidget(main, 1); rebuildNavigation(); showDetail({}); renderBooks();
    }
    void addStat(QHBoxLayout *row, const QString &name, QLabel *&number) {
        auto *frame = new QFrame; frame->setObjectName("stat");
        auto *layout = new QVBoxLayout(frame); layout->setContentsMargins(13, 11, 10, 11); layout->setSpacing(1);
        number = label("0", "statNumber", frame); layout->addWidget(number);
        auto *caption = label(name, "statLabel", frame); caption->setWordWrap(true); layout->addWidget(caption); row->addWidget(frame, 1);
    }
    bool needsReview(const QJsonObject &b) const {
        return b.value("status").toString() != "indexed" || b.value("category").toString() == "Revisar";
    }
    void rebuildNavigation() {
        clearLayout(navigation);
        QMap<QString, int> counts; int review = 0, missing = 0;
        for (const auto v : books) { const auto b = v.toObject(); counts[b.value("category").toString()]++; if (needsReview(b)) review++; if (b.value("missing").toBool()) missing++; }
        navigation->addWidget(label("SUA BIBLIOTECA", "sideHeading", sidebar)); navigation->addSpacing(8);
        addNav("Todos os livros", "__all", books.size(), "book");
        addNav("Para revisar", "__review", review, "alert"); addNav("Não encontrados", "__missing", missing, "folder");
        navigation->addSpacing(25); navigation->addWidget(label("ÁREAS DA MATEMÁTICA", "sideHeading", sidebar)); navigation->addSpacing(9);
        QStringList cats = categories;
        for (auto it = counts.cbegin(); it != counts.cend(); ++it) if (!cats.contains(it.key()) && !it.key().isEmpty()) cats << it.key();
        if (cats.isEmpty()) cats = QStringList{"Álgebra", "Análise", "Geometria", "Topologia", "Probabilidade", "Estatística", "Matemática aplicada"};
        for (const auto &cat : cats) if (cat != "Revisar") addNav(cat, cat, counts.value(cat), "");
        navigation->addStretch();
    }
    void addNav(const QString &name, const QString &key, int count, const QString &icon) {
        auto *button = new QPushButton(sidebar); button->setObjectName("nav"); button->setProperty("active", filter == key);
        button->setText(button->fontMetrics().elidedText(name, Qt::ElideRight, icon.isEmpty() ? 126 : 107) + (count ? "   " + QString::number(count) : ""));
        if (!icon.isEmpty()) { button->setIcon(lineIcon(icon, QColor("#a5b9a5"), 16)); button->setIconSize(QSize(16, 16)); }
        button->setToolTip(name + " · " + QString::number(count) + " PDFs");
        connect(button, &QPushButton::clicked, this, [this, key] { filter = key; rebuildNavigation(); applyFilter(); }); navigation->addWidget(button);
    }
    void loadCatalog() {
        runCommand({"list"}, [this](const QList<QJsonObject> &events, bool ok, const QString &error) {
            bool loaded = false;
            for (const auto &event : events) if (event.value("type") == "catalog") {
                books = event.value("books").toArray(); loaded = true; updateStats(); rebuildNavigation(); applyFilter();
                if (!scanProcess) statusLabel->setText(books.isEmpty() ? "Nenhum PDF catalogado. Use Buscar PDFs para começar." : QString("Catálogo local · %1 PDFs · %2").arg(books.size()).arg(dataDir));
            }
            if (!ok || !loaded) {
                statusLabel->setText("Não foi possível abrir o catálogo. " + error.left(180));
                if (smoke || !screenshotFile.isEmpty()) { qWarning() << "catalog load failed:" << error; QTimer::singleShot(0, qApp, [] { qApp->exit(2); }); return; }
            }
            if (loaded) finishCapture();
        });
    }
    void updateStats() {
        int review = 0, missing = 0, categorized = 0;
        for (const auto v : books) { auto b = v.toObject(); if (needsReview(b)) review++; else categorized++; if (b.value("missing").toBool()) missing++; }
        statTotal->setText(QString::number(books.size())); statCategorized->setText(QString::number(categorized)); statReview->setText(QString::number(review)); statMissing->setText(QString::number(missing));
    }
    void applyFilter() {
        filtered.clear(); const auto query = normalized(search->text().trimmed());
        for (const auto value : books) {
            const auto b = value.toObject();
            if (filter == "__review" && !needsReview(b)) continue;
            if (filter == "__missing" && !b.value("missing").toBool()) continue;
            if (!filter.startsWith("__") && b.value("category").toString() != filter) continue;
            if (!query.isEmpty()) {
                QString haystack = b.value("title").toString() + " " + b.value("author").toString() + " " + b.value("path").toString() + " " + b.value("category").toString();
                for (const auto v : b.value("keywords").toArray()) haystack += " " + v.toObject().value("word").toString();
                if (!normalized(haystack).contains(query)) continue;
            }
            filtered << b;
        }
        const int order = sort->currentIndex();
        std::sort(filtered.begin(), filtered.end(), [order](const QJsonObject &a, const QJsonObject &b) {
            if (order == 2) {
                const double ma = a.value("modified").toDouble(), mb = b.value("modified").toDouble();
                if (ma != mb) return ma > mb;
                const auto sa = a.value("modified").toString(), sb = b.value("modified").toString();
                if (sa != sb) return sa > sb;
            }
            if (order == 1 && a.value("category") != b.value("category")) return normalized(a.value("category").toString()) < normalized(b.value("category").toString());
            return normalized(a.value("title").toString()) < normalized(b.value("title").toString());
        });
        page = 0;
        const bool selectedVisible = std::any_of(filtered.cbegin(), filtered.cend(), [this](const QJsonObject &b) { return textId(b) == selectedId; });
        if (!selectedVisible) selectedId = filtered.isEmpty() ? QString() : textId(filtered.first());
        sectionTitle->setText(filter == "__all" ? "Todos os livros" : filter == "__review" ? "Para revisar" : filter == "__missing" ? "Arquivos não encontrados" : filter);
        countLabel->setText(QString::number(filtered.size()) + (filtered.size() == 1 ? " PDF" : " PDFs"));
        renderBooks(); showDetail(selectedBook());
    }
    void renderBooks() {
        if (!grid || !bookScroll) return;
        cards.clear(); clearLayout(grid);
        delete grid;
        grid = new QGridLayout(gridContainer);
        grid->setContentsMargins(0, 0, 7, 0); grid->setHorizontalSpacing(15); grid->setVerticalSpacing(16); grid->setAlignment(Qt::AlignTop);
        columns = qMax(1, qMin(4, (bookScroll->viewport()->width() - 12) / 218));
        for (int c = 0; c < 8; ++c) grid->setColumnStretch(c, c < columns ? 1 : 0);
        for (int r = 0; r < grid->rowCount(); ++r) grid->setRowMinimumHeight(r, 0);
        if (filtered.isEmpty()) {
            gridContainer->setMinimumHeight(0); gridContainer->setMaximumHeight(QWIDGETSIZE_MAX);
            auto *empty = new QWidget(gridContainer); auto *box = new QVBoxLayout(empty); box->setContentsMargins(28, 54, 28, 50); box->setSpacing(16);
            auto *mark = new QLabel(empty); mark->setAlignment(Qt::AlignCenter); mark->setPixmap(lineIcon("book", QColor("#8ea17f"), 68).pixmap(68, 68)); box->addWidget(mark);
            const bool noBooks = books.isEmpty();
            auto *title = label(noBooks ? "Um lugar para suas descobertas." : "Nenhum PDF por aqui.", "emptyTitle", empty); title->setAlignment(Qt::AlignCenter); title->setWordWrap(true); box->addWidget(title);
            auto *text = label(noBooks ? "Busque PDFs nas suas pastas para criar uma biblioteca\norganizada por áreas da matemática." : "Tente outra palavra ou escolha uma área diferente.", "emptyText", empty); text->setAlignment(Qt::AlignCenter); text->setWordWrap(true); box->addWidget(text);
            if (noBooks) {
                auto *row = new QHBoxLayout; row->addStretch(); auto *button = new QPushButton("Encontrar meus PDFs", empty); button->setObjectName("primary");
                connect(button, &QPushButton::clicked, this, [this] { startScan({}); }); row->addWidget(button); row->addStretch(); box->addLayout(row);
                auto *note = label("Os arquivos permanecem nas suas pastas originais.", "count", empty); note->setAlignment(Qt::AlignCenter); note->setWordWrap(true); box->addWidget(note);
            }
            box->addStretch(); grid->addWidget(empty, 0, 0, 1, columns); pageLabel->clear(); prevButton->hide(); nextButton->hide(); return;
        }
        const int first = page * PageSize, end = qMin(first + PageSize, filtered.size());
        const int rows = (end - first + columns - 1) / columns;
        // The scroll area must own a tall page rather than compressing all rows
        // into its viewport while a prior layout is being cleared asynchronously.
        gridContainer->setFixedHeight(rows * 286 + qMax(0, rows - 1) * 16);
        for (int r = 0; r < rows; ++r) grid->setRowMinimumHeight(r, 286);
        for (int i = first; i < end; ++i) {
            const auto b = filtered.at(i); auto *card = new BookCard(b, textId(b) == selectedId, gridContainer);
            card->clicked = [this, b] {
                selectedId = textId(b);
                for (auto *item : cards) { item->setProperty("selected", item->id == selectedId); refreshStyle(item); }
                showDetail(b);
            };
            grid->addWidget(card, (i - first) / columns, (i - first) % columns); cards << card;
        }
        grid->activate();
        pageLabel->setText(QString("%1–%2 de %3 PDFs").arg(first + 1).arg(end).arg(filtered.size()));
        prevButton->setVisible(filtered.size() > PageSize); nextButton->setVisible(filtered.size() > PageSize);
        prevButton->setEnabled(page > 0); nextButton->setEnabled(end < filtered.size());
    }
    QJsonObject selectedBook() const {
        for (const auto v : books) { auto b = v.toObject(); if (textId(b) == selectedId) return b; }
        return {};
    }
    void showDetail(const QJsonObject &book) {
        clearLayout(detailLayout);
        detailLayout->addWidget(label(book.isEmpty() ? "SUA PRÓXIMA DESCOBERTA" : "SOBRE ESTE PDF", "detailHeading", detail)); detailLayout->addSpacing(8);
        if (book.isEmpty()) {
            auto *cover = new QLabel(detail); cover->setPixmap(logoPixmap(76)); cover->setAlignment(Qt::AlignLeft); detailLayout->addWidget(cover); detailLayout->addSpacing(10);
            auto *title = label("Cada livro tem\nseu lugar.", "detailTitle", detail); title->setWordWrap(true); detailLayout->addWidget(title);
            auto *text = label("O Atlas encontra seus PDFs, lê o texto e sugere uma área da matemática com base nas palavras e expressões do documento.", "body", detail); text->setWordWrap(true); detailLayout->addWidget(text);
            detailLayout->addSpacing(14);
            for (const auto &line : QStringList{"01   Encontre seus arquivos", "02   Explore as áreas", "03   Abra o livro onde ele está"}) detailLayout->addWidget(label(line, "body", detail));
            detailLayout->addSpacing(15);
            auto *note = label("Sugestões incertas ficam em Para revisar. Você pode ajustar qualquer classificação.", "body", detail); note->setWordWrap(true); detailLayout->addWidget(note); detailLayout->addStretch(); return;
        }
        auto *cover = new BookCover(book, detail); cover->setFixedHeight(175); detailLayout->addWidget(cover);
        auto *title = label(book.value("title").toString(), "detailTitle", detail); title->setWordWrap(true); title->setTextInteractionFlags(Qt::TextSelectableByMouse); detailLayout->addWidget(title);
        auto author = book.value("author").toString(); if (!author.isEmpty()) { auto *a = label(author, "body", detail); a->setWordWrap(true); detailLayout->addWidget(a); }
        QString meta = book.value("pages").toInt() ? QString::number(book.value("pages").toInt()) + " páginas  ·  " : "";
        meta += sizeText(book.value("size").toVariant().toLongLong()); detailLayout->addWidget(label(meta, "bookMeta", detail)); detailLayout->addSpacing(7);
        detailLayout->addWidget(label(book.value("manual").toBool() ? "ÁREA AJUSTADA POR VOCÊ" : "ÁREA SUGERIDA", "detailHeading", detail));
        auto *category = label(book.value("category").toString(), needsReview(book) ? "reviewPill" : "categoryPill", detail); category->setWordWrap(true); category->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Fixed); detailLayout->addWidget(category);
        const double confidence = book.value("confidence").toDouble(); const int percent = qRound(confidence <= 1.0 ? confidence * 100 : confidence);
        detailLayout->addWidget(label(book.value("manual").toBool() ? "Classificação manual" : QString("Estimativa heurística: %1%").arg(percent), "confidence", detail));
        auto reasonText = book.value("reason").toString(); if (!reasonText.isEmpty()) { auto *reason = label(reasonText, "body", detail); reason->setWordWrap(true); detailLayout->addWidget(reason); }
        if (book.value("status").toString() == "error") { auto *error = label("Não foi possível ler o texto deste PDF. Confira o registro da busca.", "body", detail); error->setWordWrap(true); detailLayout->addWidget(error); }
        auto *change = new QPushButton("Ajustar classificação", detail); connect(change, &QPushButton::clicked, this, [this, book] { changeCategory(book); }); detailLayout->addWidget(change);
        detailLayout->addSpacing(8); detailLayout->addWidget(label("PALAVRAS MAIS FREQUENTES", "detailHeading", detail));
        const auto keywords = book.value("keywords").toArray();
        if (keywords.isEmpty()) { auto *none = label("Nenhum texto extraível encontrado.", "body", detail); none->setWordWrap(true); detailLayout->addWidget(none); }
        else {
            auto *words = new QWidget(detail); auto *wordLayout = new QGridLayout(words); wordLayout->setContentsMargins(0, 0, 0, 0); wordLayout->setSpacing(6);
            const int limit = qMin(10, keywords.size());
            for (int i = 0; i < limit; ++i) {
                const auto k = keywords.at(i).toObject(); const auto word = k.value("word").toString();
                auto *tag = label(word + "  " + QString::number(k.value("count").toInt()), "keyword", words);
                tag->setText(tag->fontMetrics().elidedText(tag->text(), Qt::ElideRight, 103)); tag->setToolTip(word + " · " + QString::number(k.value("count").toInt()) + " ocorrências");
                wordLayout->addWidget(tag, i / 2, i % 2);
            }
            detailLayout->addWidget(words);
        }
        detailLayout->addSpacing(8); detailLayout->addWidget(label("LOCAL DO ARQUIVO", "detailHeading", detail));
        const auto path = book.value("path").toString();
        auto *pathLabel = label(path, "detailPath", detail); pathLabel->setWordWrap(true); pathLabel->setTextInteractionFlags(Qt::TextSelectableByMouse); pathLabel->setToolTip(path); detailLayout->addWidget(pathLabel);
        if (book.value("missing").toBool()) { auto *missing = label("O arquivo foi movido ou removido desde a última busca.", "body", detail); missing->setWordWrap(true); detailLayout->addWidget(missing); }
        auto *open = new QPushButton(lineIcon("book", Qt::white), "Abrir PDF", detail); open->setObjectName("primary"); open->setEnabled(!book.value("missing").toBool());
        connect(open, &QPushButton::clicked, this, [this, path] { openExternal(path); }); detailLayout->addWidget(open);
        auto *row = new QHBoxLayout; row->setSpacing(6);
        auto *folder = new QPushButton(lineIcon("folder"), "Pasta", detail); folder->setToolTip("Abrir a pasta deste arquivo"); connect(folder, &QPushButton::clicked, this, [this, path] { openExternal(QFileInfo(path).absolutePath()); }); row->addWidget(folder);
        auto *copy = new QPushButton(lineIcon("copy"), "Caminho", detail); copy->setToolTip("Copiar o caminho completo"); connect(copy, &QPushButton::clicked, this, [this, path] { QApplication::clipboard()->setText(path); statusLabel->setText("Caminho copiado para a área de transferência."); }); row->addWidget(copy); detailLayout->addLayout(row); detailLayout->addStretch();
    }
    void changeCategory(const QJsonObject &book) {
        QStringList choices = categories;
        for (const auto v : books) { const auto cat = v.toObject().value("category").toString(); if (!cat.isEmpty() && !choices.contains(cat)) choices << cat; }
        if (!choices.contains("Revisar")) choices << "Revisar";
        bool accepted = false;
        const auto selected = QInputDialog::getItem(this, "Ajustar classificação", "Área da matemática para este PDF:", choices, qMax(0, choices.indexOf(book.value("category").toString())), false, &accepted);
        if (!accepted || selected.isEmpty()) return;
        runCommand({"set-category", "--id", textId(book), "--category", selected}, [this](const QList<QJsonObject> &, bool ok, const QString &error) {
            if (ok) { statusLabel->setText("Classificação atualizada."); loadCatalog(); }
            else QMessageBox::warning(this, "Classificação", "Não foi possível salvar a classificação.\n" + error.left(1500));
        });
    }
    void openExternal(const QString &path) {
        if (!QFileInfo::exists(path)) { QMessageBox::information(this, "Arquivo não encontrado", "Este caminho não está disponível. Uma nova busca atualizará o catálogo.\n\n" + path); return; }
        auto env = QProcessEnvironment::systemEnvironment();
        for (const auto &key : QStringList{"PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "LD_LIBRARY_PATH"}) env.remove(key);
        if (env.contains("ATLAS_ORIGINAL_PATH")) env.insert("PATH", env.value("ATLAS_ORIGINAL_PATH"));
        if (env.contains("ATLAS_ORIGINAL_LD_LIBRARY_PATH") && !env.value("ATLAS_ORIGINAL_LD_LIBRARY_PATH").isEmpty()) env.insert("LD_LIBRARY_PATH", env.value("ATLAS_ORIGINAL_LD_LIBRARY_PATH"));
        QProcess opener; opener.setProcessEnvironment(env); opener.setProgram("xdg-open"); opener.setArguments({path});
        if (!opener.startDetached()) QMessageBox::warning(this, "Abrir arquivo", "Não foi possível iniciar o aplicativo padrão para este arquivo.");
    }
    void setBusy(bool busy) {
        scanButton->setEnabled(!busy); folderButton->setEnabled(!busy); scanMenuButton->setEnabled(!busy); progressFrame->setVisible(busy); cancelButton->setEnabled(busy);
        if (busy) { progress->setRange(0, 0); progressLabel->setText("Procurando PDFs nas pastas selecionadas…"); }
    }
    void cancelScan() {
        if (!scanProcess || scanProcess->state() == QProcess::NotRunning || !cancelButton->isEnabled()) return;
        progressLabel->setText(closingAfterScan ? "Salvando o catálogo antes de fechar…" : "Finalizando a busca e salvando o catálogo…");
        cancelButton->setEnabled(false);
        scanProcess->terminate();
        QPointer<QProcess> proc(scanProcess);
        QTimer::singleShot(8000, this, [proc] { if (proc && proc->state() != QProcess::NotRunning) proc->kill(); });
    }
    void startScan(const QStringList &options) {
        if (scanProcess) return;
        if (demo) { QMessageBox::information(this, "Modo de demonstração", "Reabra o Atlas sem --demo para buscar arquivos reais."); return; }
        setBusy(true); pendingScanError.clear();
        scanProcess = new QProcess(this); auto *proc = scanProcess;
        auto buffer = std::make_shared<QByteArray>(); auto errors = std::make_shared<QByteArray>(); auto handled = std::make_shared<bool>(false);
        connect(proc, &QProcess::readyReadStandardOutput, this, [this, proc, buffer] {
            buffer->append(proc->readAllStandardOutput());
            int newline;
            while ((newline = buffer->indexOf('\n')) >= 0) {
                const auto line = buffer->left(newline); buffer->remove(0, newline + 1);
                const auto doc = QJsonDocument::fromJson(line); if (doc.isObject()) scanEvent(doc.object());
            }
        });
        connect(proc, &QProcess::readyReadStandardError, this, [proc, errors] { errors->append(proc->readAllStandardError()); if (errors->size() > 16000) *errors = errors->right(16000); });
        connect(proc, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, [this, proc, buffer, errors, handled](int code, QProcess::ExitStatus exit) {
            if (*handled) return;
            *handled = true;
            buffer->append(proc->readAllStandardOutput()); for (const auto &line : buffer->split('\n')) { const auto doc = QJsonDocument::fromJson(line); if (doc.isObject()) scanEvent(doc.object()); }
            errors->append(proc->readAllStandardError()); scanProcess = nullptr; setBusy(false); proc->deleteLater();
            if (closingAfterScan) { QTimer::singleShot(0, this, [this] { close(); }); return; }
            loadCatalog();
            if (!pendingScanError.isEmpty() || (code != 0 && code != 130 && exit == QProcess::NormalExit)) {
                const auto message = pendingScanError.isEmpty() ? QString::fromUtf8(*errors) : pendingScanError;
                QMessageBox::warning(this, "Registro da busca", "A busca terminou com um problema.\n\n" + message.left(2000));
            }
        });
        connect(proc, &QProcess::errorOccurred, this, [this, proc, handled](QProcess::ProcessError error) {
            if (error == QProcess::FailedToStart && !*handled) {
                *handled = true; scanProcess = nullptr; setBusy(false);
                QMessageBox::warning(this, "Iniciar busca", "Não foi possível iniciar o leitor de PDFs.\n" + proc->errorString()); proc->deleteLater();
            }
        });
        QStringList args{resources + "/backend/indexer.py", "--data-dir", dataDir, "scan"}; args << options;
        proc->setProgram(python); proc->setArguments(args); proc->start();
    }
    void scanEvent(const QJsonObject &event) {
        const auto type = event.value("type").toString();
        if (type == "progress") {
            const int current = event.value("current").toInt(), total = event.value("total").toInt();
            if (total > 0) { progress->setRange(0, total); progress->setValue(current); } else progress->setRange(0, 0);
            const auto file = QFileInfo(event.value("path").toString()).fileName();
            const auto message = event.value("message").toString();
            QString status = event.value("phase").toString() == "discover" ? "Procurando PDFs" : "Lendo e catalogando";
            if (total > 0) status += QString(" · %1 de %2").arg(current).arg(total);
            if (!file.isEmpty()) status += " · " + file;
            else if (!message.isEmpty()) status += " · " + message;
            progressLabel->setText(progressLabel->fontMetrics().elidedText(status, Qt::ElideMiddle, qMax(200, progressLabel->width() - 8)));
            progressLabel->setToolTip(event.value("path").toString());
        } else if (type == "warning") statusLabel->setText("Registro: " + event.value("message").toString().left(170));
        else if (type == "error") pendingScanError = event.value("message").toString();
        else if (type == "complete") {
            lastLog = event.value("log").toString();
            statusLabel->setText(event.value("cancelled").toBool() ? "Busca cancelada. O que já foi catalogado foi salvo." : "Busca concluída. Catálogo e registro TXT salvos.");
        }
    }
    void exportCatalog() {
        const auto output = QFileDialog::getSaveFileName(this, "Exportar catálogo e caminhos", QDir::homePath() + "/atlas-pdf-catalogo.txt", "Arquivos de texto (*.txt)");
        if (output.isEmpty()) return;
        runCommand({"export", "--output", output}, [this, output](const QList<QJsonObject> &, bool ok, const QString &error) {
            if (ok) { statusLabel->setText("Catálogo exportado para " + output); QMessageBox::information(this, "Catálogo exportado", "Os caminhos e as classificações foram salvos em:\n\n" + output); }
            else QMessageBox::warning(this, "Exportar catálogo", "Não foi possível exportar.\n" + error.left(1500));
        });
    }
    void showLogs() {
        QString path = lastLog;
        if (!QFileInfo::exists(path)) {
            QFileInfo newest;
            QDirIterator it(dataDir + "/logs", {"*.txt", "*.log"}, QDir::Files, QDirIterator::Subdirectories);
            while (it.hasNext()) { it.next(); const auto fi = it.fileInfo(); if (!newest.exists() || fi.lastModified() > newest.lastModified()) newest = fi; }
            path = newest.absoluteFilePath();
        }
        QFile log(path);
        if (path.isEmpty() || !log.open(QIODevice::ReadOnly)) { QMessageBox::information(this, "Registro da busca", "Os registros TXT aparecerão depois da primeira busca.\n\nPasta do catálogo:\n" + dataDir); return; }
        const qint64 limit = 1024 * 1024; const bool clipped = log.size() > limit; if (clipped) log.seek(log.size() - limit);
        auto *dialog = new QDialog(this); dialog->setAttribute(Qt::WA_DeleteOnClose); dialog->setWindowTitle("Atlas PDF — Registro da busca"); dialog->resize(850, 560);
        auto *layout = new QVBoxLayout(dialog); auto *pathLabel = new QLabel(path, dialog); pathLabel->setWordWrap(true); pathLabel->setTextInteractionFlags(Qt::TextSelectableByMouse); layout->addWidget(pathLabel);
        auto *text = new QPlainTextEdit(dialog); text->setReadOnly(true); text->setFont(QFont("DejaVu Sans Mono", 10)); text->setPlainText((clipped ? "[Mostrando o último 1 MB do registro]\n\n" : "") + QString::fromUtf8(log.readAll())); layout->addWidget(text, 1);
        auto *buttons = new QDialogButtonBox(QDialogButtonBox::Close, dialog); connect(buttons, &QDialogButtonBox::rejected, dialog, &QDialog::reject); layout->addWidget(buttons); dialog->show();
    }
    void finishCapture() {
        if ((screenshotFile.isEmpty() && !smoke) || screenshotScheduled) return;
        screenshotScheduled = true;
        QTimer::singleShot(550, this, [this] {
            if (smoke && !checkUi()) { qApp->exit(4); return; }
            if (!screenshotFile.isEmpty()) {
                const auto screenshot = grab();
                if (screenshot.isNull() || !screenshot.save(screenshotFile)) { qWarning() << "Could not save screenshot:" << screenshotFile; qApp->exit(3); return; }
                qInfo() << "Screenshot saved:" << screenshotFile << screenshot.size();
            }
            qInfo() << "Atlas PDF UI smoke passed; catalog records:" << books.size(); qApp->exit(0);
        });
    }
    bool checkUi() {
        auto check = [](bool passed, const char *message) {
            if (!passed) qCritical() << "Atlas UI check failed:" << message;
            return passed;
        };
        filter = "__all"; search->clear(); applyFilter();
        if (!check(filtered.size() == books.size(), "initial catalog count")) return false;
        search->setText("___atlas_smoke_absent_92d8c8f2___");
        if (!check(filtered.isEmpty(), "search excludes unmatched records")) return false;
        search->clear();
        if (!check(filtered.size() == books.size(), "clearing search restores records")) return false;
        if (!books.isEmpty()) {
            const auto first = books.first().toObject(); const auto category = first.value("category").toString();
            filter = category; applyFilter();
            if (!check(!filtered.isEmpty(), "category includes its source record")) return false;
            for (const auto &b : filtered) if (!check(b.value("category").toString() == category, "category filter excludes other areas")) return false;
            filter = "__all"; applyFilter();
            selectedId = textId(first); showDetail(first);
            if (!check(textId(selectedBook()) == textId(first), "selected record identity")) return false;
            showDetail(books.last().toObject()); showDetail(first);
        }
        const auto originalSize = size(); resize(960, 650); QApplication::processEvents(QEventLoop::ExcludeUserInputEvents); renderBooks();
        if (!check(columns >= 1 && columns <= 4, "responsive grid at minimum window width")) return false;
        resize(originalSize); QApplication::processEvents(QEventLoop::ExcludeUserInputEvents); renderBooks();
        rebuildNavigation(); showDetail(selectedBook());
        QApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete); QApplication::processEvents(QEventLoop::ExcludeUserInputEvents);
        if (!check(cards.size() == qMin(PageSize, filtered.size()), "visible page size")) return false;
        for (int i = columns; i < cards.size(); ++i) {
            if (!check(cards.at(i)->geometry().top() > cards.at(i - columns)->geometry().bottom(), "book rows have no vertical overlap")) return false;
        }
        return true;
    }
    void makeDemo() {
        categories = QStringList{"Álgebra linear", "Cálculo", "Geometria", "Probabilidade", "Estatística"};
        for (int i = 0; i < 8; ++i) {
            const auto cat = categories.at(i % categories.size());
            QJsonObject b{{"id", i + 1}, {"title", cat + " — demonstração " + QString::number(i + 1)}, {"path", "/exemplo/demonstracao-" + QString::number(i + 1) + ".pdf"}, {"category", cat}, {"confidence", 0.8}, {"pages", 120 + i * 27}, {"size", 2048000}, {"status", "indexed"}, {"reason", "Registro de demonstração. Não representa um arquivo no computador."}, {"keywords", QJsonArray{QJsonObject{{"word", "matemática"}, {"count", 42}}}}};
            books.append(b);
        }
        setWindowTitle("Atlas PDF — DEMONSTRAÇÃO"); statusLabel->setText("DEMONSTRAÇÃO · Registros de exemplo, ativados explicitamente com --demo."); updateStats(); rebuildNavigation(); applyFilter(); finishCapture();
    }
};

int main(int argc, char **argv) {
    QApplication::setAttribute(Qt::AA_EnableHighDpiScaling); QApplication::setAttribute(Qt::AA_UseHighDpiPixmaps);
    QApplication app(argc, argv); app.setApplicationName("Atlas PDF"); app.setOrganizationName("Atlas"); app.setApplicationVersion("1.0.0");
    QCommandLineParser parser; parser.setApplicationDescription("Atlas PDF — Catálogo local de PDFs e áreas da matemática."); parser.addHelpOption(); parser.addVersionOption();
    parser.addOption({"data-dir", "Pasta persistente do catálogo e dos registros.", "path"});
    parser.addOption({"screenshot", "Salvar a interface em PNG e sair.", "path"});
    parser.addOption({"smoke", "Carregar o catálogo, verificar a interface e sair."});
    parser.addOption({"demo", "Exibir registros de demonstração claramente identificados."}); parser.process(app);
    QString resources = qEnvironmentVariable("ATLAS_RESOURCE_DIR");
    if (resources.isEmpty()) {
        const QStringList candidates{QCoreApplication::applicationDirPath() + "/..", QCoreApplication::applicationDirPath(), QDir::currentPath()};
        for (const auto &path : candidates) if (QFileInfo::exists(path + "/backend/indexer.py")) { resources = QDir(path).absolutePath(); break; }
        if (resources.isEmpty()) resources = QDir(QCoreApplication::applicationDirPath() + "/..").absolutePath();
    }
    QString dataDir = parser.value("data-dir");
    if (dataDir.isEmpty()) dataDir = QDir(QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation)).filePath("atlas-pdf");
    dataDir = QDir(dataDir).absolutePath();
    const QDir fontDir(resources + "/assets/fonts");
    for (const auto &font : fontDir.entryList({"*.ttf", "*.otf"}, QDir::Files)) QFontDatabase::addApplicationFont(fontDir.absoluteFilePath(font));
    QFile theme(resources + "/ui/theme.qss"); if (theme.open(QIODevice::ReadOnly)) app.setStyleSheet(QString::fromUtf8(theme.readAll()));
    app.setStyle(QStyleFactory::create("Fusion")); QPixmapCache::setCacheLimit(48 * 1024);
    AtlasWindow window(resources, dataDir, parser.value("screenshot"), parser.isSet("smoke"), parser.isSet("demo")); window.show(); return app.exec();
}
