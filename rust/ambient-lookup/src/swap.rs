//! `--swap`: rewrite a draft with the Spanish for the words that can be swapped safely.
//!
//! The assistant's draft never changes; this runs on the display side. Only a word with a single
//! Spanish candidate is swapped, and only where its English part of speech is clear from the
//! neighbours, because nothing here can tell "a cap" from "to cap". Each swapped word is wrapped
//! in sentinels so the hover mod can underline it: `OPEN id SEP shown CLOSE`.

use std::collections::HashSet;

use crate::index::Index;
use crate::scan::{occurrences, Occurrence};

pub const OPEN: char = '\u{E000}';
pub const SEP: char = '\u{E001}';
pub const CLOSE: char = '\u{E002}';

/// Words before a noun that mark it as a noun.
const BEFORE_NOUN: &[&str] = &[
    "the", "a", "an", "this", "that", "these", "those", "my", "your", "his", "her", "its", "our",
    "their", "some", "any", "each", "every", "no", "another", "one", "two", "three", "four", "five",
    "many", "few", "several", "both", "all", "other", "of", "in", "on", "at", "for", "with", "from",
    "by", "about", "into", "through", "per", "without", "than", "like",
];
/// A noun-looking word after one of these is a verb ("it caps", "that works", "will cap").
const NOT_BEFORE_NOUN: &[&str] = &[
    "it", "he", "she", "i", "we", "you", "they", "that", "which", "who", "this", "there", "what", "to",
    "will", "would", "can", "could", "shall", "should", "may", "might", "must", "do", "does", "did",
    "not", "cannot",
];
/// A word followed by one of these takes an object, so it is a verb ("cap the", "limit them").
const OBJECT_STARTS: &[&str] = &[
    "the", "a", "an", "this", "that", "these", "those", "it", "them", "him", "her", "me", "us", "you",
    "my", "your", "our", "their", "its", "his",
];
/// A bare verb after one of these is an infinitive in Spanish too: "can add" -> "can añadir".
const BEFORE_BARE_VERB: &[&str] = &[
    "will", "would", "can", "could", "shall", "should", "may", "might", "must", "cannot", "can't",
    "won't", "don't", "doesn't", "didn't", "wouldn't", "shouldn't", "couldn't", "let's", "please",
];
/// After one of these (or one of these plus an intensifier) a word is an adjective: "is claro".
const BE_FORMS: &[&str] = &[
    "is", "are", "was", "were", "be", "been", "being", "am", "seem", "seems", "seemed", "feel",
    "feels", "felt", "look", "looks", "looked", "become", "becomes", "became", "remain", "remains",
    "remained", "stay", "stays", "stayed", "get", "gets", "got",
];
const INTENSIFIERS: &[&str] = &[
    "very", "too", "so", "quite", "really", "more", "most", "less", "rather", "fairly", "not",
    "also", "always", "still", "just", "even", "already", "usually", "now", "then", "never",
];
/// Number words and quantifiers read badly swapped alone: "one by uno", "a poco smarter".
const KEEP_ENGLISH: &[&str] = &[
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "little", "few",
    "several", "half",
];
/// Common adjectives the vocabulary may not hold; a noun after one is a noun ("a small feature").
const ADJECTIVES: &[&str] = &[
    "new", "old", "big", "small", "large", "good", "bad", "great", "long", "short", "high", "low",
    "same", "other", "first", "last", "next", "main", "whole", "full", "real", "free", "hard",
    "easy", "simple", "single", "final", "key", "top", "best", "own", "major", "minor", "common",
    "general", "special", "basic", "public", "private", "local", "recent", "current", "previous",
    "similar", "different", "various", "specific", "multiple", "extra", "additional", "typical",
    "early", "late", "total", "second", "third", "different", "important", "useful", "clear",
];
/// A word with up to this many Spanish candidates of the right kind is swapped for the first listed.
const MAX_SYNONYMS: usize = 3;
/// Subject pronouns and the person of the Spanish present they select.
const SUBJECTS: &[(&str, Person)] = &[
    ("i", Person::First),
    ("you", Person::Second),
    ("we", Person::FirstPlural),
    ("they", Person::ThirdPlural),
    ("it", Person::Third),
    ("he", Person::Third),
    ("she", Person::Third),
    ("this", Person::Third),
    ("that", Person::Third),
    ("which", Person::Third),
    ("who", Person::Third),
];
/// Verbs whose present is not the regular pattern although the stem looks regular.
const IRREGULAR_PRESENT: &[&str] = &["jugar", "reunir", "prohibir", "aislar", "rehusar", "cohibir", "aunar"];
/// Verbs in -iar/-uar that are written without an accent in the present.
const REGULAR_IAR: &[&str] = &["cambiar", "estudiar", "copiar", "limpiar", "iniciar", "anunciar", "odiar", "apreciar", "negociar"];
/// Only the first person (`yo`) is irregular.
const IRREGULAR_YO: &[&str] = &["salir", "valer", "caber"];
const ADJECTIVE_SUFFIXES: &[&str] = &["ous", "ive", "ful", "less", "able", "ible", "ical", "ish"];

/// A "to" after one of these is a preposition, not the start of an infinitive.
const BEFORE_TO_PREPOSITION: &[&str] = &[
    "back", "up", "down", "according", "due", "next", "prior", "close", "similar", "compared",
    "thanks", "opposed", "equal", "key", "answer", "reply", "listen", "go", "went", "come", "came",
];

pub fn swap(index: &Index, text: &str) -> String {
    let skipped = skipped_ranges(text);
    let words = Words { adjectives: adjective_words(index), nouns: noun_words(index) };
    let mut out = String::with_capacity(text.len());
    let mut cursor = 0;
    for o in occurrences(index, text) {
        if skipped.iter().any(|&(a, b)| o.start < b && a < o.end) {
            continue;
        }
        let Some((start, shown)) = replacement(index, &words, text, &o) else { continue };
        // An infinitive swallows its "to", which may already sit inside an earlier span or a skipped one.
        if start < cursor || skipped.iter().any(|&(a, b)| start < b && a < o.start) {
            continue;
        }
        out.push_str(&text[cursor..start]);
        out.push_str(&shown);
        cursor = o.end;
    }
    out.push_str(&text[cursor..]);
    out
}

/// A single plain word that is not grammar ("it" and "that" appear in some glosses).
fn is_content_word(alt: &str) -> bool {
    let lower = alt.to_lowercase();
    alt.chars().all(char::is_alphabetic)
        && lower.len() > 2
        && ![NOT_BEFORE_NOUN, OBJECT_STARTS, BE_FORMS, INTENSIFIERS, BEFORE_NOUN, BEFORE_BARE_VERB]
            .iter()
            .any(|list| list.contains(&lower.as_str()))
}

/// English words of the vocabulary's adjectives: what a following noun can follow.
fn adjective_words(index: &Index) -> HashSet<String> {
    let mut words: HashSet<String> = ADJECTIVES.iter().map(|w| w.to_string()).collect();
    for entry in index.entries.iter().filter(|e| e.kind == "adjective") {
        for alt in entry.english.split(" / ") {
            if is_content_word(alt) {
                words.insert(alt.to_lowercase());
            }
        }
    }
    words
}

/// English words of the vocabulary's nouns, for noun-noun compounds ("vocabulary file").
fn noun_words(index: &Index) -> HashSet<String> {
    let mut words = HashSet::new();
    for entry in index.entries.iter().filter(|e| e.kind == "noun") {
        for alt in entry.english.split(" / ") {
            if is_content_word(alt) {
                words.insert(alt.to_lowercase());
            }
        }
    }
    words
}

/// Lookup tables built once per draft.
struct Words {
    adjectives: HashSet<String>,
    nouns: HashSet<String>,
}

/// The lowercase words before `pos` on the same line, nearest first, up to `n`.
fn words_before(text: &str, pos: usize, n: usize) -> Vec<String> {
    let is_word = |c: char| c.is_alphabetic() || c == '\'' || c == '\u{2019}';
    let mut words = Vec::new();
    let mut rest = &text[..pos];
    while words.len() < n {
        let trimmed = rest.trim_end_matches([' ', '\t']);
        let start = trimmed.char_indices().rev().take_while(|&(_, c)| is_word(c)).last().map(|(i, _)| i);
        let Some(start) = start else { break };
        words.push(trimmed[start..].replace('\u{2019}', "'").to_lowercase());
        rest = &trimmed[..start];
    }
    words
}

/// The byte range's start and the text that replaces `o` from there.
///
/// The surrounding words decide which part of speech the English word is; only the candidates of
/// that kind are kept, and the word is swapped when exactly one Spanish word is left.
fn replacement(index: &Index, words: &Words, text: &str, o: &Occurrence) -> Option<(usize, String)> {
    // A hyphen joins a compound.
    if text[..o.start].ends_with('-') || text[o.end..].starts_with('-') {
        return None;
    }
    let surface = &text[o.start..o.end];
    let lower = surface.to_lowercase();
    if KEEP_ENGLISH.contains(&lower.as_str())
        || surface.chars().skip(1).any(|c| c.is_uppercase())
        || !surface.chars().any(char::is_alphabetic)
    {
        return None;
    }
    // "Claude Code" is a name; a capital mid-sentence stays English, except language names.
    if surface.starts_with(char::is_uppercase)
        && !["english", "spanish"].contains(&lower.as_str())
        && !starts_sentence(text, o.start)
    {
        return None;
    }
    let before = words_before(text, o.start, 4);
    let after = neighbour(text[o.end..].chars(), false);
    let first_before = before.first().map(String::as_str);
    let second_before = before.get(1).map(String::as_str);
    let after_object = after.as_deref().is_some_and(|w| OBJECT_STARTS.contains(&w));
    let before_is = |set: &[&str]| first_before.is_some_and(|w| set.contains(&w));

    // Candidates that can be swapped at all: one Spanish word, no sense note in the gloss.
    let usable: Vec<(usize, bool)> = o
        .candidates
        .iter()
        .zip(&o.inflected)
        .map(|(&at, &inflected)| (at, inflected))
        .filter(|&(at, _)| {
            let e = &index.entries[at];
            !e.spanish.contains(' ') && !e.english.contains('(')
        })
        .collect();

    // What the neighbours say the word is, most certain first.
    enum Role {
        InfinitiveTo,
        BareVerb,
        Present(Person),
        Adjective,
        Noun { singular_ok: bool, plural_ok: bool },
        Attributive,
        Connector,
        Adverb,
    }
    let mut roles: Vec<Role> = Vec::new();
    if first_before == Some("to") && infinitive_start(text, o.start).is_some() {
        roles.push(Role::InfinitiveTo);
    }
    let after_not_modal = before_is(&["not"]) && second_before.is_some_and(|w| BEFORE_BARE_VERB.contains(&w) || ["do", "does", "did"].contains(&w));
    if before_is(BEFORE_BARE_VERB) || after_not_modal || (first_before.is_none() && after_object) {
        roles.push(Role::BareVerb);
    }
    // A determiner or adjective before the word proves it is a noun; another noun only suggests it.
    let strong = first_before.is_some_and(|w| BEFORE_NOUN.contains(&w) || words.adjectives.contains(w) || ends_like_adjective(w));
    let gloss_len = |kind: &str| usable.iter().filter(|&&(at, _)| index.entries[at].kind == kind).map(|&(at, _)| index.entries[at].english.split(" / ").count()).min();
    // "Spanish words": the adjective ("Spanish") fits better than the noun ("Spanish / Castilian").
    let adjective_fits = gloss_len("adjective").is_some_and(|a| gloss_len("noun").is_none_or(|n| a < n));
    // "you add", "it returns the words": a Spanish present, when the verb is regular.
    let subject = subject_person(words, &before);
    let surface_s = lower.ends_with('s') && !lower.ends_with("ss");
    if let Some((person, pronoun)) = subject {
        let third_s = person == Person::Third && surface_s;
        let bare = (person != Person::Third || !surface_s) && pronoun;
        if (third_s && (pronoun || after_object)) || bare {
            roles.push(Role::Present(person));
        }
    }
    let marked = strong
        || first_before.is_some_and(|w| words.nouns.contains(w) && !w.ends_with('s') && !NOT_BEFORE_NOUN.contains(&w));
    // "a custom number": a noun before another noun is often a modifier, so its sense is a guess.
    let noun_follows = after.as_deref().is_some_and(|w| words.nouns.contains(w) && !w.ends_with('s'));
    // "returns" after a noun is the verb; only a determiner or adjective makes it a plural noun.
    let verb_form = usable.iter().any(|&(at, inflected)| inflected && index.entries[at].kind == "verb");
    if adjective_fits && after.as_deref().is_some_and(|w| !w.ends_with('s')) && first_before != Some("to") {
        roles.push(Role::Attributive);
    }
    // "time listening": after a plain noun, an -ing word is more likely a participle than a noun.
    if marked && (strong || !lower.ends_with("ing")) {
        roles.push(Role::Noun { singular_ok: !noun_follows, plural_ok: strong || !verb_form });
    }
    if !after_object {
        // "is not very claro": intensifiers may pile up between the verb and the adjective
        let be = before
            .iter()
            .find(|w| !INTENSIFIERS.contains(&w.as_str()))
            .is_some_and(|w| BE_FORMS.contains(&w.as_str()));
        if be {
            roles.push(Role::Adjective);
        }
        if !marked && !before_is(NOT_BEFORE_NOUN) && !verb_form {
            roles.push(Role::Noun { singular_ok: false, plural_ok: true });
        }
        let noun_too = usable.iter().any(|&(at, _)| index.entries[at].kind == "noun");
        if !noun_too && first_before != Some("to") && after.as_deref().is_some_and(|w| !w.ends_with('s')) {
            roles.push(Role::Attributive);
        }
    }
    roles.push(Role::Connector);
    if !after_object {
        roles.push(Role::Adverb);
    }

    for role in roles {
        let chosen: Vec<(usize, bool)> = usable
            .iter()
            .copied()
            .filter(|&(at, inflected)| {
                let e = &index.entries[at];
                match (&role, e.kind.as_str()) {
                    (Role::InfinitiveTo | Role::BareVerb, "verb") => !inflected,
                    (Role::Present(person), "verb") => {
                        (inflected == (*person == Person::Third && surface_s)) && conjugate(&e.spanish, *person).is_some()
                    }
                    (Role::Adjective, "adjective") => !inflected,
                    (Role::Attributive, "adjective") => !inflected && !e.spanish.ends_with('o'),
                    (Role::Noun { singular_ok, plural_ok }, "noun") => if inflected { *plural_ok } else { *singular_ok },
                    (Role::Connector, "connector") => !inflected,
                    (Role::Adverb, "adverb") => !inflected && usable.iter().all(|&(a, _)| index.entries[a].kind == "adverb"),
                    _ => false,
                }
            })
            .collect();
        let mut chosen = chosen;
        // The tightest gloss ("term") beats a looser one ("deadline / term"); ties keep vocabulary order.
        chosen.sort_by_key(|&(at, _)| index.entries[at].english.split(" / ").count());
        let mut spanish: Vec<&str> = chosen.iter().map(|&(at, _)| index.entries[at].spanish.as_str()).collect();
        spanish.dedup();
        spanish.dedup();
        if spanish.is_empty() || spanish.len() > MAX_SYNONYMS {
            continue;
        }
        let (at, inflected) = chosen[0];
        let entry = &index.entries[at];
        let from = match role {
            Role::InfinitiveTo => infinitive_start(text, o.start)?,
            _ => o.start,
        };
        let mut shown = match role {
            Role::Present(person) => conjugate(&entry.spanish, person)?,
            _ if inflected => plural_of(&entry.spanish)?,
            _ => entry.spanish.clone(),
        };
        if text[from..].starts_with(char::is_uppercase) {
            let mut chars = shown.chars();
            shown = chars.next().map(|c| c.to_uppercase().chain(chars).collect())?;
        }
        return Some((from, format!("{OPEN}{}{SEP}{shown}{CLOSE}", entry.id)));
    }
    None
}

/// Whether `pos` opens a sentence, line or list item.
fn starts_sentence(text: &str, pos: usize) -> bool {
    let head = text[..pos].trim_end_matches([' ', '\t', '*', '_', '#', '>', '-', '"', '(', '[']);
    head.chars().last().is_none_or(|c| matches!(c, '.' | '!' | '?' | ':' | '\n') || c.is_ascii_digit())
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum Person {
    First,
    Second,
    Third,
    FirstPlural,
    ThirdPlural,
}

/// The regular Spanish present of `infinitive`, or None when the verb might change its stem,
/// carry an accent or have an irregular form (anything this cannot tell is left in English).
fn conjugate(infinitive: &str, person: Person) -> Option<String> {
    let kind = infinitive.get(infinitive.len().checked_sub(2)?..)?;
    let stem = &infinitive[..infinitive.len() - 2];
    if !["ar", "er", "ir"].contains(&kind) || stem.is_empty() || IRREGULAR_PRESENT.contains(&infinitive) {
        return None;
    }
    if (kind == "ar" && (stem.ends_with(['i', 'u']) && !stem.ends_with("gu") && !stem.ends_with("qu")) && !REGULAR_IAR.contains(&infinitive))
        || infinitive.ends_with("uir")
        || infinitive.ends_with("guir")
        || infinitive.ends_with("quir")
    {
        return None;
    }
    // Stem changes (e>ie, o>ue, e>i) only happen on a stressed e or o: leave those verbs alone.
    let last_vowel = stem.chars().rev().find(|c| "aeiouáéíóú".contains(*c))?;
    if !"aiu".contains(last_vowel) {
        return None;
    }
    let ending = match (person, kind) {
        (Person::First, _) => {
            if stem.ends_with(['g', 'c']) || infinitive.ends_with("aer") || IRREGULAR_YO.contains(&infinitive) {
                return None;
            }
            "o"
        }
        (Person::Second, "ar") => "as",
        (Person::Second, _) => "es",
        (Person::Third, "ar") => "a",
        (Person::Third, _) => "e",
        (Person::FirstPlural, "ar") => "amos",
        (Person::FirstPlural, "er") => "emos",
        (Person::FirstPlural, _) => "imos",
        (Person::ThirdPlural, "ar") => "an",
        (Person::ThirdPlural, _) => "en",
    };
    Some(format!("{stem}{ending}"))
}

/// The subject's person if one of `words` (nearest first) is a subject pronoun, or a singular
/// noun for the third person; an adverb may stand between the subject and the verb.
fn subject_person(words: &Words, before: &[String]) -> Option<(Person, bool)> {
    let mut at = 0;
    if before.first().is_some_and(|w| INTENSIFIERS.contains(&w.as_str()) || (w.ends_with("ly") && w.len() > 4)) {
        at = 1;
    }
    let w = before.get(at)?;
    if let Some(&(_, person)) = SUBJECTS.iter().find(|(name, _)| name == w) {
        return Some((person, true));
    }
    (words.nouns.contains(w.as_str()) && !w.ends_with('s')).then_some((Person::Third, false))
}

fn ends_like_adjective(word: &str) -> bool {
    ADJECTIVE_SUFFIXES.iter().any(|s| word.len() > s.len() + 2 && word.ends_with(s))
}

/// Where the "to" that opens an infinitive before `start` begins, unless it is a preposition.
fn infinitive_start(text: &str, start: usize) -> Option<usize> {
    let head = text[..start].trim_end_matches([' ', '\t']);
    let to_start = head.len().checked_sub(2)?;
    let to = head.get(to_start..)?;
    if !to.eq_ignore_ascii_case("to") || head[..to_start].chars().last().is_some_and(|c| c.is_alphanumeric() || c == '\'' || c == '-') {
        return None;
    }
    // After these "to" is a preposition ("from start to finish"), so the next word is a noun.
    let preposed = neighbour(text[..to_start].chars().rev(), true);
    if preposed.as_deref().is_some_and(|w| BEFORE_TO_PREPOSITION.contains(&w)) {
        return None;
    }
    // "from start to open", "between A and B to ...": a range, so "to" is a preposition.
    let clause = text[..to_start].rsplit(['.', ',', ';', ':', '!', '?', '\n']).next().unwrap_or("");
    if clause.split_whitespace().any(|w| w.eq_ignore_ascii_case("from") || w.eq_ignore_ascii_case("between")) {
        return None;
    }
    Some(to_start)
}

/// The lowercase word next to a span on the same line, or None across punctuation or a line break.
fn neighbour(chars: impl Iterator<Item = char>, reversed: bool) -> Option<String> {
    let mut word: Vec<char> = Vec::new();
    let mut gap = false;
    for c in chars {
        if word.is_empty() && !gap && matches!(c, ' ' | '\t') {
            continue;
        }
        if c.is_alphabetic() || c == '\'' || c == '\u{2019}' {
            word.push(if c == '\u{2019}' { '\'' } else { c });
            gap = true;
        } else {
            break;
        }
    }
    if word.is_empty() {
        return None;
    }
    if reversed {
        word.reverse();
    }
    Some(word.into_iter().flat_map(char::to_lowercase).collect())
}

/// The regular Spanish plural, or None where stress or an invariable `-s` makes it a guess.
fn plural_of(word: &str) -> Option<String> {
    let last = word.chars().last()?;
    if "aeiou".contains(last) {
        return Some(format!("{word}s"));
    }
    if let Some(stem) = word.strip_suffix("ión") {
        return Some(format!("{stem}iones"));
    }
    if let Some(stem) = word.strip_suffix('z') {
        return Some(format!("{stem}ces"));
    }
    let accented = word.chars().any(|c| "áéíóúü".contains(c));
    (!accented && !"sx".contains(last) && last.is_alphabetic()).then(|| format!("{word}es"))
}

/// Table rows (field names stay as written) and double-quoted text (quotations are never altered).
fn skipped_ranges(text: &str) -> Vec<(usize, usize)> {
    let mut ranges = Vec::new();
    let mut offset = 0;
    for line in text.split_inclusive('\n') {
        let end = offset + line.len();
        if line.trim_start().starts_with('|') {
            ranges.push((offset, end));
        } else {
            let mut open: Option<usize> = None;
            for (i, c) in line.char_indices() {
                match (c, open) {
                    ('"', None) | ('\u{201c}', None) => open = Some(offset + i),
                    ('"', Some(a)) | ('\u{201d}', Some(a)) => {
                        ranges.push((a, offset + i + c.len_utf8()));
                        open = None;
                    }
                    _ => {}
                }
            }
            if let Some(a) = open {
                ranges.push((a, end));
            }
        }
        offset = end;
    }
    ranges
}

#[cfg(test)]
mod tests {
    use super::*;

    const VOCAB: &str = "## noun (6)\n\
ventana | ventana | window\n\
herramienta | herramienta | tool\n\
roca | roca | rock (music)\n\
cancion | canción | song\n\
trabajo | trabajo | work\n\
termino | término | term\n\
plazo | plazo | deadline / term\n\
## verb (7)\n\
trabajar | trabajar | to work\n\
usar | usar | to use\n\
abrir | abrir | to open\n\
escribir | escribir | to write\n\
empezar | empezar | to start\n\
enviar | enviar | to send\n\
cambiar | cambiar | to change\n\
## adjective (3)\n\
perfecto | perfecto | perfect\n\
nuevo | nuevo | new\n\
diferente | diferente | different\n\
## adverb (2)\n\
rapido | rápido | quickly\n\
nunca | nunca | never\n\
## connector (1)\n\
porque | porque | because\n";

    /// Swapped words print as `[spanish]`.
    fn run(text: &str) -> String {
        let out = swap(&Index::parse(VOCAB), text);
        let mut shown = String::new();
        let mut inside = false;
        let mut after_sep = false;
        for c in out.chars() {
            match c {
                OPEN => inside = true,
                SEP => {
                    after_sep = true;
                    shown.push('[');
                }
                CLOSE => {
                    inside = false;
                    after_sep = false;
                    shown.push(']');
                }
                _ if inside && !after_sep => {}
                _ => shown.push(c),
            }
        }
        shown
    }

    #[test]
    fn swaps_nouns_after_a_determiner_and_inflects_plurals() {
        assert_eq!(run("Read the window."), "Read the [ventana].");
        assert_eq!(run("Two songs and a tool"), "Two [canciones] and a [herramienta]");
        assert_eq!(run("Windows and Tool"), "[Ventanas] and Tool");
    }

    #[test]
    fn the_neighbours_pick_the_part_of_speech() {
        // `work` is a noun and a verb; the context says which
        assert_eq!(run("the work"), "the [trabajo]");
        assert_eq!(run("It is time to work"), "It is time [trabajar]");
        assert_eq!(run("it can work"), "it can [trabajar]");
    }

    #[test]
    fn an_ing_word_after_a_plain_noun_is_not_a_noun() {
        let vocab = "## noun (1)\nescucha | escucha | listening\n";
        let out = swap(&Index::parse(vocab), "this time listening, the listening");
        assert_eq!(out.matches(OPEN).count(), 1);
    }

    #[test]
    fn leaves_unclear_words_alone() {
        // a singular noun with no determiner could be a verb
        assert_eq!(run("It will window"), "It will window");
        assert_eq!(run("to tool the thing"), "to tool the thing");
        // followed by an object, so a verb
        assert_eq!(run("Read tool the thing"), "Read tool the thing");
    }

    #[test]
    fn the_tightest_gloss_wins_among_synonyms() {
        assert_eq!(run("the term"), "the [término]");
    }

    #[test]
    fn swaps_an_infinitive_together_with_its_to() {
        assert_eq!(run("You need to open the tool"), "You need [abrir] the [herramienta]");
        assert_eq!(run("To open it, try this"), "[Abrir] it, try this");
        assert_eq!(run("I want to  open it"), "I want [abrir] it");
    }

    #[test]
    fn a_modal_or_an_imperative_takes_the_infinitive() {
        assert_eq!(run("You can open it"), "You can [abrir] it");
        assert_eq!(run("It does not work"), "It does not [trabajar]");
        assert_eq!(run("Open the window"), "[Abrir] the [ventana]");
    }

    #[test]
    fn conjugated_verbs_follow_a_subject() {
        assert_eq!(run("You write it"), "You [escribes] it");
        assert_eq!(run("We write it"), "We [escribimos] it");
        assert_eq!(run("They write it"), "They [escriben] it");
        assert_eq!(run("It uses the tool"), "It [usa] the [herramienta]");
        // a noun subject with an object after the verb
        assert_eq!(run("The song opens the window"), "The [canción] [abre] the [ventana]");
    }

    #[test]
    fn leaves_verbs_that_could_change_their_stem_or_accent() {
        assert_eq!(run("You start it"), "You start it");
        assert_eq!(run("They send it"), "They send it");
        assert_eq!(run("She changed it and used it"), "She changed it and used it");
    }

    #[test]
    fn conjugation_is_regular_or_nothing() {
        assert_eq!(conjugate("usar", Person::Second).as_deref(), Some("usas"));
        assert_eq!(conjugate("cambiar", Person::Third).as_deref(), Some("cambia"));
        assert_eq!(conjugate("añadir", Person::FirstPlural).as_deref(), Some("añadimos"));
        assert_eq!(conjugate("escribir", Person::First).as_deref(), Some("escribo"));
        for irregular in ["empezar", "poder", "volver", "enviar", "jugar", "construir", "ser", "ir", "ver", "pedir"] {
            assert_eq!(conjugate(irregular, Person::Third), None, "{irregular}");
        }
        // the first person is irregular in these
        for irregular in ["hacer", "salir", "traer", "escoger"] {
            assert_eq!(conjugate(irregular, Person::First), None, "{irregular}");
        }
    }

    #[test]
    fn adjectives_follow_a_form_of_to_be_and_bare_connectors_swap() {
        assert_eq!(run("It is perfect and runs quickly"), "It is [perfecto] and runs [rápido]");
        assert_eq!(run("It is not very perfect"), "It is not very [perfecto]");
        assert_eq!(run("It works because it opens"), "It [trabaja] [porque] it [abre]");
    }

    #[test]
    fn an_adjective_before_a_noun_swaps_only_when_it_has_no_gender() {
        // `nuevo` would need agreement; `diferente` has none
        assert_eq!(run("a new window"), "a new [ventana]");
        assert_eq!(run("a different window"), "a [diferente] [ventana]");
    }

    #[test]
    fn names_numbers_and_quotes_stay() {
        assert_eq!(run("Claude Window is a Tool"), "Claude Window is a Tool");
        assert_eq!(run("It never opens one tool"), "It [nunca] [abre] one [herramienta]");
    }

    #[test]
    fn never_touches_code_tables_quotes_or_paths() {
        assert_eq!(run("Run `the window` now"), "Run `the window` now");
        assert_eq!(run("| the window |\n| a tool |"), "| the window |\n| a tool |");
        assert_eq!(run("He said \"the window\" twice"), "He said \"the window\" twice");
        assert_eq!(run("See the window/tool.txt"), "See the window/tool.txt");
    }

    #[test]
    fn leaves_glosses_with_a_sense_note_and_compounds_alone() {
        assert_eq!(run("the rock"), "the rock");
        assert_eq!(run("a tool-based setup"), "a tool-based setup");
    }

    #[test]
    fn plurals_are_only_built_when_regular() {
        assert_eq!(plural_of("ventana").as_deref(), Some("ventanas"));
        assert_eq!(plural_of("canción").as_deref(), Some("canciones"));
        assert_eq!(plural_of("lápiz").as_deref(), Some("lápices"));
        assert_eq!(plural_of("papel").as_deref(), Some("papeles"));
        assert_eq!(plural_of("lunes"), None);
        assert_eq!(plural_of("jardín"), None);
        assert_eq!(plural_of("autobús"), None);
    }
}
