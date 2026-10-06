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
    let mut i = 0;
    while i < line.len() {
        let rest = &line[i..];
        if rest.starts_with('`') {
            if let Some(close) = rest[1..].find('`') {
                ranges.push((base + i, base + i + close + 2));
                i += close + 2;
                continue;
            }
        } else if rest.starts_with("http://") || rest.starts_with("https://") {
            let len = rest
                .find(|c: char| c.is_whitespace() || c == ')' || c == '>')
                .unwrap_or(rest.len());
            ranges.push((base + i, base + i + len));
            i += len;
            continue;
        }
        i += rest.chars().next().map_or(1, char::len_utf8);
    }
}

fn tokenize(index: &Index, text: &str) -> Vec<Token> {
    let excluded = excluded_ranges(text);
    let mut tokens = Vec::new();
    let mut start: Option<usize> = None;
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    for (i, &(at, c)) in chars.iter().enumerate() {
        let inner_apostrophe = (c == '\'' || c == '\u{2019}')
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

    #[test]
    fn invalid_utf8_is_lossy() {
        let s = String::from_utf8_lossy(b"open \xff window").into_owned();
        assert_eq!(words(&s), ["open", "window"]);
    }
}
