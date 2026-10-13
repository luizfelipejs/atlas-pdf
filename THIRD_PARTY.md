# Componentes incluídos

O Atlas PDF inclui componentes com licenças próprias. Os avisos disponíveis no
sistema ficam em `licenses/` dentro do executável extraído. As licenças do
Electron e do Chromium ficam em `electron/LICENSE` e
`electron/LICENSES.chromium.html`. As bibliotecas são carregadas dinamicamente;
`manifest.json` lista as dependências incluídas e as versões dos runtimes.

| Componente | Função | Referência |
| --- | --- | --- |
| Electron / Chromium / Node.js | Janela desktop e runtime web | https://www.electronjs.org/docs/latest/tutorial/about |
| React / React DOM | Interface | https://github.com/facebook/react/blob/main/LICENSE |
| Motion | Animações | https://github.com/motiondivision/motion/blob/main/LICENSE.md |
| Lucide | Ícones SVG | https://lucide.dev/license |
| Vite | Build da interface, apenas desenvolvimento | https://github.com/vitejs/vite/blob/main/LICENSE |
| CPython 3.12 | Indexação, SQLite e classificação | https://docs.python.org/3.12/license.html |
| Sentence Transformers multilingual MiniLM L12 v2 | Embeddings multilíngues locais; modelo Apache-2.0 | https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 |
| ONNX Runtime 1.22.1 | Inferência em CPU; MIT | https://github.com/microsoft/onnxruntime/blob/main/LICENSE |
| NumPy 2.2.6 | Vetores e centroides; BSD | https://numpy.org/doc/stable/license.html |
| Hugging Face Tokenizers 0.21.4 | Tokenização local; Apache-2.0 | https://github.com/huggingface/tokenizers/blob/main/LICENSE |
| Bash / GNU findutils | Descoberta de PDFs | https://www.gnu.org/licenses/gpl-3.0.html |
| Poppler | Metadados, texto e miniaturas | https://poppler.freedesktop.org/ |
| DejaVu Fonts | Tipografia local | https://dejavu-fonts.github.io/License.html |

O pacote inclui os avisos dos pacotes JavaScript e Python usados em produção.
O modelo e o tokenizer têm revisão e SHA-256 registrados em
`assets/models/minilm/manifest.json`, com licença e documentação nessa pasta.
A glibc,
o carregador ELF, drivers gráficos e a sessão de desktop vêm do sistema.
O sandbox exige namespaces permitidos ou um helper SUID compatível já instalado
pelo Chrome/Chromium; esse helper do sistema não é redistribuído pelo Atlas.
A versão Qt anterior foi preservada separadamente em `dist/AtlasPDF-qt-backup`.
