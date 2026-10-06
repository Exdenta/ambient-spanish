//! Find vocabulary matches in a draft and render them.

use crate::index::{is_stop, Index};
use serde_json::json;
use std::collections::HashSet;

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

/// Sorted, disjoint byte ranges that are never prose: fenced code, inline
/// code, URLs, emails, paths, filenames, identifiers and blockquotes.
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
    let mut i = 0;
    while i < line.len() {
        let rest = &line[i..];
        let c = rest.chars().next().unwrap();
        if c.is_whitespace() {
            i += c.len_utf8();
        } else if c == '`' {
            // A run of n backticks closes at the next run of exactly n on the
            // line; with no closing run the span ends at the end of the line.
            let n = rest.bytes().take_while(|&b| b == b'`').count();
            let mut end = line.len();
            let mut j = i + n;
            while j < line.len() {
                if line.as_bytes()[j] == b'`' {
                    let run = line[j..].bytes().take_while(|&b| b == b'`').count();
                    if run == n {
                        end = j + run;
                        break;
                    }
                    j += run;
                } else {
                    j += 1;
                }
            }
            ranges.push((base + i, base + end));
            i = end;
        } else {
            let len = rest.find(|c: char| c.is_whitespace() || c == '`').unwrap_or(rest.len());
            chunk_ranges(&rest[..len], base + i, ranges);
            i += len;
        }
    }
}

/// A whitespace-delimited chunk: carve out any `scheme://` URL (which ends at
/// whitespace, `)` or `>`), then judge the pieces around it.
fn chunk_ranges(chunk: &str, at: usize, ranges: &mut Vec<(usize, usize)>) {
    let scheme_char = |c: char| c.is_ascii_alphanumeric() || c == '+' || c == '.' || c == '-';
    let mut piece = 0;
    let mut search = 0;
    while let Some(found) = chunk[search..].find("://") {
        let p = search + found;
        let sb = chunk[piece..p]
            .char_indices()
            .rev()
            .find(|&(_, c)| !scheme_char(c))
            .map_or(piece, |(k, c)| piece + k + c.len_utf8());
        if sb < p {
            let end = chunk[p..].find([')', '>']).map_or(chunk.len(), |k| p + k);
            piece_range(&chunk[piece..sb], at + piece, ranges);
            ranges.push((at + sb, at + end));
            piece = end;
            search = end;
        } else {
            search = p + 3;
        }
    }
    piece_range(&chunk[piece..], at + piece, ranges);
}

fn piece_range(piece: &str, at: usize, ranges: &mut Vec<(usize, usize)>) {
    if !piece.is_empty() && is_code_chunk(piece) {
        ranges.push((at, at + piece.len()));
    }
}

fn is_alnum(c: char) -> bool {
    c.is_alphanumeric()
}

/// Whether a chunk (judged without its surrounding punctuation) is a path,
/// email, URL, filename or code identifier rather than prose.
fn is_code_chunk(raw: &str) -> bool {
    let mut t = raw.trim_start_matches(|c| "([{<\"'\u{201c}\u{2018}*".contains(c));
    t = t.trim_end_matches(|c| ")]}>\"'\u{201d}\u{2019},.;:!?*\u{2026}".contains(c));
    if let Some(inner) = t.strip_prefix('_').and_then(|x| x.strip_suffix('_')) {
        if !t.starts_with("__") && !inner.is_empty() && !inner.contains('_') {
            t = inner;
        }
    }
    let b = t.as_bytes();
    if b.len() < 2 {
        return false;
    }
    let lower = t.to_ascii_lowercase();
    if lower.starts_with("www.") || lower.starts_with("mailto:") || lower.contains("://") {
        return true;
    }
    // handles, issue numbers, flags
    if b[0] == b'@' && t[1..].starts_with(is_alnum) {
        return true;
    }
    if b[0] == b'#' && b[1..].iter().all(u8::is_ascii_digit) {
        return true;
    }
    if t.starts_with("--") && t[2..].starts_with(is_alnum) {
        return true;
    }
    if b[0] == b'-'
        && b[1].is_ascii_alphanumeric()
        && b.iter().all(|&c| c.is_ascii_alphanumeric() || b"-=_.:/,".contains(&c))
    {
        return true;
    }
    if is_email(t) {
        return true;
    }
    // paths
    if b[0] == b'/' || t.starts_with("./") || t.starts_with("../") || t.starts_with("~/") {
        return true;
    }
    if b.len() >= 3 && b[0].is_ascii_alphabetic() && b[1] == b':' && (b[2] == b'\\' || b[2] == b'/') {
        return true;
    }
    if (1..b.len() - 1).any(|i| b[i] == b'\\') {
        return true;
    }
    if b.iter().filter(|&&c| c == b'/').count() >= 2 {
        return true;
    }
    // filenames and dotted names, but not abbreviations like e.g. or a.m.
    if t.contains('.') {
        let parts: Vec<&str> = t.split('.').collect();
        let abbreviation = parts.iter().all(|p| p.chars().count() == 1 && p.starts_with(char::is_alphabetic));
        if !abbreviation {
            let ext = parts[parts.len() - 1];
            let stem = &t[..t.len() - ext.len() - 1];
            let stem_ok = stem.chars().next_back().is_some_and(|c| is_alnum(c) || c == '_');
            let ext_ok = (1..=5).contains(&ext.len())
                && ext.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit());
            if ext_ok && stem_ok {
                return true;
            }
            let dotted = parts.len() >= 2
                && parts.iter().all(|p| !p.is_empty() && p.chars().all(|c| is_alnum(c) || c == '_' || c == '$'));
            if dotted {
                return true;
            }
            // hidden files: .gitignore, .env.local
            if b[0] == b'.' && t[1..].starts_with(is_alnum) {
                return true;
            }
        }
    }
    // identifiers: snake_case, CONSTANT_CASE, camelCase, PascalCase, __dunder__
    if t.starts_with("__") && t[2..].starts_with(is_alnum) {
        return true;
    }
    let mut prev: Option<char> = None;
    let mut chars = t.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '_' {
            if prev.is_some_and(is_alnum) && chars.peek().copied().is_some_and(is_alnum) {
                return true;
            }
        } else if prev.is_some_and(char::is_lowercase) && c.is_uppercase() {
            return true;
        }
        prev = Some(c);
    }
    false
}

fn is_email(t: &str) -> bool {
    let Some(at) = t.find('@') else { return false };
    let (local, domain) = (&t[..at], &t[at + 1..]);
    !local.is_empty()
        && local.bytes().all(|c| c.is_ascii_alphanumeric() || b"._%+-".contains(&c))
        && domain.contains('.')
        && domain.split('.').all(|p| !p.is_empty() && p.chars().all(|c| is_alnum(c) || c == '-'))
}

fn tokenize(index: &Index, text: &str) -> Vec<Token> {
    let excluded = excluded_ranges(text);
    let mut tokens = Vec::new();
    let mut cursor = 0;
    let mut start: Option<usize> = None;
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    for (i, &(at, c)) in chars.iter().enumerate() {
        let inner_apostrophe = (c == '\'' || c == '\u{2019}')
            && start.is_some()
            && chars.get(i + 1).is_some_and(|&(_, n)| n.is_alphabetic());
        let word_char = c.is_alphabetic() || inner_apostrophe;
        match (word_char, start) {
            (true, None) => start = Some(at),
            (false, Some(s)) => {
                push_token(index, text, s, at, &excluded, &mut cursor, &mut tokens);
                start = None;
            }
            _ => {}
        }
    }
    if let Some(s) = start {
        push_token(index, text, s, text.len(), &excluded, &mut cursor, &mut tokens);
    }
    tokens
}

fn push_token(
    index: &Index,
    text: &str,
    start: usize,
    end: usize,
    excluded: &[(usize, usize)],
    cursor: &mut usize,
    tokens: &mut Vec<Token>,
) {
    // Ranges are sorted and disjoint and tokens arrive in order, so one
    // cursor walks them in linear time.
    while *cursor < excluded.len() && excluded[*cursor].1 <= start {
        *cursor += 1;
    }
    if excluded.get(*cursor).is_some_and(|&(a, _)| a <= start) {
        return;
    }
    let raw = &text[start..end];
    let mut end = end;
    let mut surface = raw.replace('\u{2019}', "'");
    for apos in ["'s", "\u{2019}s", "'S", "\u{2019}S"] {
        if raw.len() > apos.len() && raw.ends_with(apos) {
            end -= apos.len();
            surface = text[start..end].replace('\u{2019}', "'");
            break;
        }
    }
    let norm = index.norm(&surface);
    tokens.push(Token { start, end, surface, norm });
}

/// Only spaces or tabs on one line, or a bare hyphen, may sit between the
/// words of one phrase. A line break, list marker, quote marker or spaced
/// dash ends the phrase.
fn joined(text: &str, left: &Token, right: &Token) -> bool {
    let gap = &text[left.end..right.start];
    gap == "-" || (!gap.is_empty() && gap.chars().all(|c| c == ' ' || c == '\t' || c == '\u{a0}'))
}

pub fn find_matches(index: &Index, text: &str) -> Vec<Match> {
    let tokens = tokenize(index, text);
    let mut found: Vec<Match> = Vec::new();
    let mut seen_text: HashSet<String> = HashSet::new();
    let mut seen_sets: HashSet<String> = HashSet::new();
    let mut i = 0;
    while i < tokens.len() {
        let mut advanced = false;
        let longest = index.max_words.min(tokens.len() - i);
        for len in (1..=longest).rev() {
            let window = &tokens[i..i + len];
            if !window.windows(2).all(|pair| joined(text, &pair[0], &pair[1])) {
                continue;
            }
            if len == 1 {
                let t = &window[0];
                let lower = t.surface.to_lowercase();
                if is_stop(&lower) || is_stop(&index.base(&lower)) || is_stop(&t.norm) {
                    continue;
                }
            }
            let key = window.iter().map(|t| t.norm.as_str()).collect::<Vec<_>>().join(" ");
            let Some(forms) = index.forms.get(&key) else { continue };
            let surfaces: Vec<&str> = window.iter().map(|t| t.surface.as_str()).collect();
            let mut candidates: Vec<usize> = Vec::new();
            for (at, form) in forms {
                if index.key_ok(&surfaces, form) && !candidates.contains(at) {
                    candidates.push(*at);
                }
            }
            if candidates.is_empty() {
                continue;
            }
            let span = text[window[0].start..window[len - 1].end].to_string();
            let set_key = {
                let mut ids = candidates.clone();
                ids.sort_unstable();
                ids.iter().map(|n| n.to_string()).collect::<Vec<_>>().join(",")
            };
            if seen_text.insert(span.to_lowercase()) && seen_sets.insert(set_key) {
                found.push(Match { text: span, candidates });
            }
            i += len;
            advanced = true;
            break;
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

    #[test]
    fn non_ascii_input_does_not_panic() {
        assert_eq!(words("it’s the doctor’s car — open the window"), ["open", "window"]);
        assert_eq!(words("open — window"), ["open", "window"]);
        assert_eq!(words("café 😀 ñandú open"), ["open"]);
        assert!(words("`é` and https://é.com/é ok").is_empty());
    }

    #[test]
    fn curly_apostrophe_matches_straight() {
        assert_eq!(words("the window’s frame"), ["window"]);
        assert_eq!(words("the window's frame"), ["window"]);
    }

    #[test]
    fn grammar_words_are_never_matched() {
        let index = Index::parse("be | ser | to be\nhave | tener | to have\ncan | poder | can\nit | lo | it\nas | como | as\n");
        // lines above have no kind heading; still parsed
        for t in ["We use it as is", "I can see that I have a book", "was does has not", "of in on at for with from by up"] {
            assert!(find_matches(&index, t).is_empty(), "{t}");
        }
    }

    #[test]
    fn stemmer_overreach_is_rejected() {
        assert!(words("openers and openness").is_empty());
        let index = Index::parse("## noun\namigo | amigo | friend\nsimpatico | simpático | friendly\nsierra | sierra | saw\n## verb\nver | ver | to see\n");
        let f = find_matches(&index, "My friends have seen it");
        let got: Vec<_> = f.iter().map(|m| m.text.as_str()).collect();
        assert_eq!(got, ["friends", "seen"]);
        assert_eq!(f[0].candidates.len(), 1);
        assert_eq!(f[1].candidates.len(), 1);
    }

    #[test]
    fn possessive_and_plural_collapse() {
        assert_eq!(words("window's Windows window"), ["window"]);
    }

    const BIG: &str = "## verb (8)\n\
cerrar | cerrar | to close\n\
ir | ir | to go\n\
estudiar | estudiar | to study\n\
salir | salir | to leave / go out\n\
dejar | dejar | to leave / to let\n\
ascender | ascender | to rise / be promoted\n\
girar | girar | to turn\n\
soportar | soportar | to bear / stand\n\
## noun (7)\n\
casa | casa | house\n\
ciudad | ciudad | city\n\
caja | caja | box\n\
oso | oso | bear\n\
subida | subida | rise / climb\n\
rosa | rosa | rose\n\
izquierda | izquierda | left\n\
## adjective (2)\n\
izquierdo | izquierdo | left\n\
bueno | bueno | good\n\
## adverb (2)\n\
incluso | incluso | even\n\
abajo | abajo | down\n\
## connector (1)\n\
por-lo-tanto | por lo tanto | therefore\n\
## phrase (5)\n\
buenos-dias | buenos días | good morning\n\
tener-en-cuenta | tener en cuenta | to bear in mind\n\
ponerse-al-dia | ponerse al día | to catch up\n\
al-final | al final | in the long run\n\
estar-al-tanto | estar al tanto | to be up to date / aware\n";

    fn big(text: &str) -> Vec<(String, Vec<String>)> {
        let index = Index::parse(BIG);
        find_matches(&index, text)
            .into_iter()
            .map(|m| {
                let sp = m.candidates.iter().map(|&a| index.entries[a].spanish.clone()).collect();
                (m.text, sp)
            })
            .collect()
    }

    fn big_words(text: &str) -> Vec<String> {
        big(text).into_iter().map(|(t, _)| t).collect()
    }

    fn spanish_of(text: &str, word: &str) -> Vec<String> {
        big(text).into_iter().find(|(t, _)| t == word).map(|(_, s)| s).unwrap_or_default()
    }

    #[test]
    fn phrases_never_cross_lines_or_markers() {
        assert_eq!(big_words("good morning"), ["good morning"]);
        assert_eq!(big_words("good  \t morning"), ["good  \t morning"]);
        assert_eq!(big_words("- good\n- morning\n"), ["good"]);
        assert_eq!(big_words("good\nmorning"), ["good"]);
        assert_eq!(big_words("good\r\nmorning"), ["good"]);
        assert_eq!(big_words("good\r\n\r\nmorning"), ["good"]);
        assert_eq!(big_words("good\n\nmorning"), ["good"]);
        assert_eq!(big_words("* good\n* morning"), ["good"]);
        assert_eq!(big_words("good - morning"), ["good"]);
        assert_eq!(big_words("1. good\n2. morning"), ["good"]);
        let index = Index::parse(BIG);
        for t in ["- good\n- morning\n", "good\r\n\r\nmorning", "to bear\nin mind"] {
            for m in find_matches(&index, t) {
                assert!(!m.text.contains('\n') && !m.text.contains('\r'), "{t:?}");
            }
            assert!(!render_text(&index, &find_matches(&index, t)).trim_end().contains("\n\n"));
        }
    }

    #[test]
    fn phrases_starting_with_to_match_with_and_without_it() {
        assert_eq!(big_words("Bear in mind that it works"), ["Bear in mind"]);
        assert_eq!(spanish_of("Bear in mind that it works", "Bear in mind"), ["tener en cuenta"]);
        assert_eq!(big_words("We want to bear in mind that"), ["to bear in mind"]);
        assert_eq!(big_words("She bears in mind that"), ["bears in mind"]);
        assert_eq!(big_words("We caught up yesterday"), ["caught up"]);
        assert_eq!(big_words("We will catch up"), ["catch up"]);
        assert_eq!(big_words("I am catching up"), ["catching up"]);
        assert_eq!(big_words("It is up to date"), ["is up to date"]);
        assert_eq!(big_words("Try to be up to date"), ["to be up to date"]);
        // the head inflects, the rest does not
        assert!(big_words("caught ups").is_empty());
        // a lone verb still matches its own entry
        assert_eq!(big_words("The bear sleeps"), ["bear"]);
    }

    #[test]
    fn inflection_follows_the_entry_kind() {
        // false positives that must disappear
        assert!(big_words("this evening").is_empty());
        assert!(big_words("some boxing").is_empty());
        assert!(big_words("ups and downs").is_empty());
        assert!(big_words("the roses").iter().all(|w| w == "roses"));
        assert!(!spanish_of("A rose is red", "rose").contains(&"subida".to_string()));
        // "rose" never reaches the noun rise (subida), only the noun and verb senses
        assert_eq!(spanish_of("It rose", "rose"), ["ascender", "rosa"]);
        // correct matches that stay
        assert_eq!(spanish_of("it closes", "closes"), ["cerrar"]);
        assert_eq!(spanish_of("it closed", "closed"), ["cerrar"]);
        assert_eq!(spanish_of("it is closing", "closing"), ["cerrar"]);
        assert_eq!(spanish_of("two houses", "houses"), ["casa"]);
        assert_eq!(spanish_of("big cities", "cities"), ["ciudad"]);
        assert_eq!(spanish_of("she went home", "went"), ["ir"]);
        assert_eq!(spanish_of("it has gone", "gone"), ["ir"]);
        assert_eq!(spanish_of("studying", "studying"), ["estudiar"]);
        assert_eq!(spanish_of("studied", "studied"), ["estudiar"]);
        assert_eq!(spanish_of("the boxes", "boxes"), ["caja"]);
        assert_eq!(spanish_of("go down", "down"), ["abajo"]);
        assert_eq!(spanish_of("even so", "even"), ["incluso"]);
    }

    #[test]
    fn an_ambiguous_word_keeps_every_sense() {
        // "left" is the adjective or the past of leave; the reply's writer picks
        let got = spanish_of("turn left", "left");
        assert_eq!(got, ["salir", "dejar", "izquierda", "izquierdo"]);
        assert!(big_words("turn left").contains(&"turn".to_string()));
        // other irregular verb forms keep working when nothing spells them
        assert_eq!(spanish_of("she went", "went"), ["ir"]);
        // and the verb's own forms still reach leave
        assert_eq!(spanish_of("it leaves", "leaves"), ["salir", "dejar"]);
        assert_eq!(spanish_of("people leaving", "leaving"), ["salir", "dejar"]);
    }

    fn none(texts: &[&str]) {
        for t in texts {
            assert!(words(t).is_empty(), "{t:?} -> {:?}", words(t));
        }
    }

    #[test]
    fn urls_are_skipped() {
        none(&[
            "see ftp://open.com/window",
            "file:///open/window",
            "ssh://open@window.com",
            "mailto:open@window.com",
            "visit www.open.com",
            "(https://open.com/window)",
            "see https://open.com/window, ok",
        ]);
        assert_eq!(words("[open](https://window.com/open) window"), ["open", "window"]);
    }

    #[test]
    fn emails_are_skipped() {
        none(&["write user@open.com", "mail (work@window.org)."]);
        assert_eq!(words("mail user@open.com about the window"), ["window"]);
    }

    #[test]
    fn paths_are_skipped() {
        none(&[
            "./open/window.py",
            "see ../open",
            "run ~/open",
            "edit /open now",
            "C:\\Users\\open\\window.txt",
            "D:/open",
            "open\\window",
            "open/window/work",
            "src/window.rs",
            "(src/open.rs)",
        ]);
        assert_eq!(words("check ./open/window.py and open it"), ["open"]);
    }

    #[test]
    fn single_slash_between_words_is_prose() {
        assert_eq!(words("open/close"), ["open"]);
        assert_eq!(words("read/write the window"), ["window"]);
        assert_eq!(words("work/rock"), ["work", "rock"]);
    }

    #[test]
    fn filenames_are_skipped_but_abbreviations_are_not() {
        none(&["window.py", "see config.json.", "open.rs", "my-window.txt", "edit .window", "the open.h1 file"]);
        assert_eq!(words("the window."), ["window"]);
        assert_eq!(words("e.g. open, i.e. window, etc. work"), ["open", "window", "work"]);
        assert_eq!(words("at 5 p.m. open the window"), ["open", "window"]);
    }

    #[test]
    fn identifiers_are_skipped() {
        none(&[
            "alpha_open",
            "OPEN_WINDOW",
            "__init__",
            "openWindow",
            "WindowManager",
            "iPhone_open",
            "os.path",
            "config.window.size",
            "--open",
            "-o",
            "(--window)",
            "@open",
            "#123",
            "fix (#123)",
        ]);
        assert_eq!(words("pass --open to the window"), ["window"]);
        assert_eq!(words("thanks @open for the work"), ["work"]);
    }

    #[test]
    fn double_and_unterminated_backticks() {
        none(&["``open window``", "``open ` window``", "```open window```"]);
        assert_eq!(words("``open`` window"), ["window"]);
        assert_eq!(words("open `window"), ["open"]);
        assert!(words("`open window").is_empty());
        assert_eq!(words("`open window\nwork"), ["work"]);
        assert_eq!(words("`open` and `window` work"), ["work"]);
    }

    #[test]
    fn punctuated_prose_still_matches() {
        assert_eq!(words("Open the window, then close it."), ["Open", "window"]);
        assert_eq!(words("the window's frame"), ["window"]);
        assert_eq!(words("(open window)"), ["open", "window"]);
        assert_eq!(words("Open: yes."), ["Open"]);
        assert_eq!(words("\"open\" and 'window'"), ["open", "window"]);
        assert_eq!(words("an open-window policy"), ["open", "window"]);
        assert_eq!(words("the work-in-progress"), ["work"]);
        assert_eq!(words("open it... window"), ["open", "window"]);
        assert_eq!(words("**open** _window_"), ["open", "window"]);
        assert_eq!(words("- open\n- window"), ["open", "window"]);
        assert_eq!(big_words("Bear in mind, good morning."), ["Bear in mind", "good morning"]);
        assert_eq!(big_words("good-morning"), ["good-morning"]);
    }

    #[test]
    fn exclusion_is_linear() {
        let line = "`open` ./a/b.py user@x.com window\n";
        let text = line.repeat(60_000);
        let started = std::time::Instant::now();
        let found = words(&text);
        assert_eq!(found, ["window"]);
        assert!(started.elapsed().as_secs() < 5);
    }

    #[test]
    fn invalid_utf8_is_lossy() {
        let s = String::from_utf8_lossy(b"open \xff window").into_owned();
        assert_eq!(words(&s), ["open", "window"]);
    }
}
