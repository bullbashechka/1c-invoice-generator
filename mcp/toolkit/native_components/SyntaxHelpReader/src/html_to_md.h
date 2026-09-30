#pragma once

#include <string>

namespace syntax_help {

class HbkIndex;

// Convert HTML to Markdown.
// Links are resolved via index.ResolveLinkToBreadcrumb().
// Resolved links get "topic:" prefix: [text](topic:breadcrumb)
// Unresolved links are rendered as plain text.
std::string HtmlToMarkdown(const std::string& html,
                            HbkIndex& index,
                            const std::string& base_html_path,
                            const std::string& source_path);

} // namespace syntax_help
