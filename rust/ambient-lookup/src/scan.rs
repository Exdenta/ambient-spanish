//! Find vocabulary matches in a draft and render them.

use crate::index::{is_stop, Index};
use serde_json::json;

pub struct Match {
    pub text: String,
    pub candidates: Vec<usize>,
}

struct Token {
    start: usize,
    end: usize,
    surface: String,
    norm: String,
}

/// Byte ranges that are never prose: fenced code, inline code, URLs and
/// blockquotes.
fn excluded_ranges(text: &str) -> Vec<(usize, usize)> {
    let mut ranges = Vec::new();
    let mut offset = 0;
    let mut fence_start: Option<usize> = None;
    for line in text.split_inclusive('\n') {
        let end = offset + line.len();
        let trimmed = line.trim_start();
        if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
            match fence_start.take() {
                Some(start) => ranges.push((start, end)),
                None => fence_start = Some(offset),
            }
        } else if fence_start.is_none() {
            if trimmed.starts_with('>') {
                ranges.push((offset, end));
            } else {
                inline_ranges(line, offset, &mut ranges);
            }
        }
        offset = end;
    }
    if let Some(start) = fence_start {
        ranges.push((start, text.len()));
    }
    ranges
}

fn inline_ranges(line: &str, base: usize, ranges: &mut Vec<(usize, usize)>) {
    let bytes = line.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'`' {
            if let Some(close) = line[i + 1..].find('`') {
                ranges.push((base + i, base + i + close + 2));
                i += close + 2;
                continue;
            }
        } else if line[i..].starts_with("http://") || line[i..].starts_with("https://") {
            let len = line[i..]
                .find(|c: char| c.is_whitespace() || c == ')' || c == '>')
                .unwrap_or(line.len() - i);
            ranges.push((base + i, base + i + len));
            i += len;
            continue;
        }
        i += 1;
    }
}

fn tokenize(index: &Index, text: &str) -> Vec<Token> {
    let excluded = excluded_ranges(text);
    let mut tokens = Vec::new();
    let mut start: Option<usize> = None;
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    for (i, &(at, c)) in chars.iter().enumerate() {
        let inner_apostrophe = c == '\''
            && start.is_some()
            && chars.get(i + 1).map_or(false, |&(_, n)| n.is_alphabetic());
        let word_char = c.is_alphabetic() || inner_apostrophe;
        match (word_char, start) {
            (true, None) => start = Some(at),
            (false, Some(s)) => {
                push_token(index, text, s, at, &excluded, &mut tokens);
                start = None;
            }
            _ => {}
        }
    }
    if let Some(s) = start {
        push_token(index, text, s, text.len(), &excluded, &mut tokens);
    }
    tokens
}

fn push_token(
    index: &Index,
    text: &str,
    start: usize,
    end: usize,
    excluded: &[(usize, usize)],
    tokens: &mut Vec<Token>,
) {
    if excluded.iter().any(|&(a, b)| start >= a && start < b) {
        return;
    }
    let surface = text[start..end].to_string();
    let norm = index.norm(&surface);
    tokens.push(Token { start, end, surface, norm });
}

/// Only whitespace or a hyphen may sit between the words of one phrase.
fn joined(text: &str, left: &Token, right: &Token) -> bool {
    text[left.end..right.start].chars().all(|c| c.is_whitespace() || c == '-')
        && !text[left.end..right.start].contains("\n\n")
}

pub fn find_matches(index: &Index, text: &str) -> Vec<Match> {
    let tokens = tokenize(index, text);
    let mut found: Vec<Match> = Vec::new();
    let mut i = 0;
    while i < tokens.len() {
        let mut advanced = false;
        let longest = index.max_words.min(tokens.len() - i);
        for len in (1..=longest).rev() {
            let window = &tokens[i..i + len];
            if !window.windows(2).all(|pair| joined(text, &pair[0], &pair[1])) {
                continue;
            }
            if len == 1 && is_stop(&window[0].surface.to_lowercase()) {
                continue;
            }
            let key = window.iter().map(|t| t.norm.as_str()).collect::<Vec<_>>().join(" ");
            if let Some(candidates) = index.map.get(&key) {
                let span = text[window[0].start..window[len - 1].end].to_string();
                let lower = span.to_lowercase();
                if !found.iter().any(|m| m.text.to_lowercase() == lower) {
                    found.push(Match { text: span, candidates: candidates.clone() });
                }
                i += len;
                advanced = true;
                break;
            }
        }
        if !advanced {
            i += 1;
        }
    }
    found
}

/// One line per distinct English word or phrase: `text: spanish | spanish`.
/// Only the dictionary forms, to confirm they are in the vocabulary; the writer
/// adapts them (conjugation, gender, number) and picks the sense that fits.
pub fn render_text(index: &Index, matches: &[Match]) -> String {
    let mut out = String::new();
    for m in matches {
        let mut words: Vec<&str> = Vec::new();
        for &at in &m.candidates {
            let spanish = index.entries[at].spanish.as_str();
            if !words.contains(&spanish) {
                words.push(spanish);
            }
        }
        out.push_str(&format!("{}: {}\n", m.text, words.join(" | ")));
    }
    out
}

pub fn render_json(index: &Index, matches: &[Match]) -> String {
    let rows: Vec<_> = matches
        .iter()
        .map(|m| {
            let candidates: Vec<_> = m
                .candidates
                .iter()
                .map(|&at| {
                    let e = &index.entries[at];
                    json!({"id": e.id, "spanish": e.spanish, "english": e.english, "kind": e.kind})
                })
                .collect();
            json!({"text": m.text, "candidates": candidates})
        })
        .collect();
    format!("{}\n", json!({"count": rows.len(), "matches": rows}))
}

#[cfg(test)]
mod tests {
    use super::*;

    const VOCAB: &str = "# header\n\
## verb (3)\n\
abrir | abrir | to open\n\
buscar | buscar | to look for / search\n\
trabajar | trabajar | to work / function\n\
## noun (3)\n\
ventana | ventana | window\n\
trabajo | trabajo | work\n\
roca | roca | rock (music)\n\
## adverb (1)\n\
rapido | rápido | quickly\n\
## connector (1)\n\
por-lo-tanto | por lo tanto | therefore\n";

    fn words(text: &str) -> Vec<String> {
        let index = Index::parse(VOCAB);
        find_matches(&index, text).into_iter().map(|m| m.text).collect()
    }

    #[test]
    fn matches_inflected_forms_and_phrases() {
        assert_eq!(words("She opened the windows quickly."), ["opened", "windows", "quickly"]);
        assert_eq!(words("I was looking for it"), ["looking for"]);
        assert_eq!(words("I searched everywhere"), ["searched"]);
    }

    #[test]
    fn ambiguous_words_keep_every_candidate() {
        let index = Index::parse(VOCAB);
        let found = find_matches(&index, "it does work");
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].candidates.len(), 2);
    }

    #[test]
    fn grammar_words_never_match_alone() {
        assert_eq!(words("It is to be done"), Vec::<String>::new());
    }

    #[test]
    fn code_urls_and_quotes_are_skipped() {
        assert!(words("Run `open window` now").is_empty());
        assert!(words("```\nopen window\n```").is_empty());
        assert!(words("See https://example.com/open-window").is_empty());
        assert!(words("> open the window").is_empty());
    }

    #[test]
    fn repeated_words_are_listed_once() {
        assert_eq!(words("open it, then open it again"), ["open"]);
    }

    #[test]
    fn output_lists_ids_for_recording() {
        let index = Index::parse(VOCAB);
        let text = render_text(&index, &find_matches(&index, "open the window"));
        assert!(text.contains("open: abrir\n"));
        assert!(text.contains("window: ventana\n"));
        assert!(!text.contains('#') && !text.contains('('));
    }
}
