//! The vocabulary and its English-side lookup table.

use rust_stemmers::{Algorithm, Stemmer};
use std::collections::{HashMap, HashSet};

pub struct Entry {
    pub id: String,
    pub spanish: String,
    pub english: String,
    pub kind: String,
}

pub struct Index {
    pub entries: Vec<Entry>,
    /// Normalised English key (stemmed words joined by a space) to entries.
    pub map: HashMap<String, Vec<usize>>,
    /// Words in the longest key, which bounds the phrase search.
    pub max_words: usize,
    stemmer: Stemmer,
}

/// Words that carry English grammar, not content. Substituting them would turn
/// the sentence into Spanish, so a single one of them never matches on its own.
const STOP: &[&str] = &[
    "be", "do", "will", "would", "should", "could", "may", "might", "must", "shall", "to", "the",
    "a", "an",
];

const IRREGULAR: &[(&str, &str)] = &[
    ("am", "be"), ("is", "be"), ("are", "be"), ("was", "be"), ("were", "be"), ("been", "be"),
    ("being", "be"), ("did", "do"), ("does", "do"), ("done", "do"), ("doing", "do"),
    ("had", "have"), ("has", "have"), ("having", "have"), ("went", "go"), ("gone", "go"),
    ("goes", "go"), ("going", "go"), ("saw", "see"), ("seen", "see"), ("made", "make"),
    ("took", "take"), ("taken", "take"), ("gave", "give"), ("given", "give"),
    ("found", "find"), ("thought", "think"), ("came", "come"), ("got", "get"),
    ("gotten", "get"), ("said", "say"), ("told", "tell"), ("wrote", "write"),
    ("written", "write"), ("ate", "eat"), ("eaten", "eat"), ("drank", "drink"),
    ("drunk", "drink"), ("ran", "run"), ("began", "begin"), ("begun", "begin"),
    ("built", "build"), ("bought", "buy"), ("brought", "bring"), ("left", "leave"),
    ("kept", "keep"), ("lost", "lose"), ("met", "meet"), ("paid", "pay"), ("sent", "send"),
    ("spoke", "speak"), ("spoken", "speak"), ("spent", "spend"), ("stood", "stand"),
    ("understood", "understand"), ("wore", "wear"), ("worn", "wear"), ("won", "win"),
    ("taught", "teach"), ("sat", "sit"), ("slept", "sleep"), ("heard", "hear"),
    ("felt", "feel"), ("held", "hold"), ("knew", "know"), ("known", "know"),
    ("chose", "choose"), ("chosen", "choose"), ("drove", "drive"), ("driven", "drive"),
    ("flew", "fly"), ("flown", "fly"), ("forgot", "forget"), ("forgotten", "forget"),
    ("grew", "grow"), ("grown", "grow"), ("hid", "hide"), ("hidden", "hide"),
    ("led", "lead"), ("lay", "lie"), ("lain", "lie"), ("rose", "rise"), ("risen", "rise"),
    ("sold", "sell"), ("shook", "shake"), ("shown", "show"), ("sang", "sing"),
    ("sung", "sing"), ("swam", "swim"), ("threw", "throw"), ("thrown", "throw"),
    ("woke", "wake"), ("woken", "wake"), ("children", "child"), ("men", "man"),
    ("women", "woman"), ("people", "person"), ("feet", "foot"), ("teeth", "tooth"),
];

pub fn is_stop(word: &str) -> bool {
    STOP.contains(&word)
}

impl Index {
    pub fn parse(raw: &str) -> Index {
        let irregular: HashMap<&str, &str> = IRREGULAR.iter().copied().collect();
        let mut index = Index {
            entries: Vec::new(),
            map: HashMap::new(),
            max_words: 1,
            stemmer: Stemmer::create(Algorithm::English),
        };
        let mut kind = String::new();
        for line in raw.lines() {
            if let Some(rest) = line.strip_prefix("## ") {
                kind = rest.split_whitespace().next().unwrap_or("").to_string();
                continue;
            }
            if line.starts_with('#') || line.trim().is_empty() {
                continue;
            }
            let parts: Vec<&str> = line.split(" | ").map(str::trim).collect();
            if parts.len() != 3 || parts.iter().any(|p| p.is_empty()) {
                continue;
            }
            let at = index.entries.len();
            index.entries.push(Entry {
                id: parts[0].to_string(),
                spanish: parts[1].to_string(),
                english: parts[2].to_string(),
                kind: kind.clone(),
            });
            let mut seen = HashSet::new();
            for key in english_keys(parts[2], &kind) {
                let words: Vec<&str> = key.split(' ').collect();
                if words.len() == 1 && is_stop(words[0]) {
                    continue;
                }
                let normalised = words
                    .iter()
                    .map(|w| index.normalise(w, &irregular))
                    .collect::<Vec<_>>()
                    .join(" ");
                if seen.insert(normalised.clone()) {
                    index.max_words = index.max_words.max(words.len());
                    index.map.entry(normalised).or_default().push(at);
                }
            }
        }
        index
    }

    fn normalise(&self, word: &str, irregular: &HashMap<&str, &str>) -> String {
        let lower = word.to_lowercase();
        let base = irregular.get(lower.as_str()).copied().unwrap_or(&lower);
        self.stemmer.stem(base).into_owned()
    }

    /// Normalised form of one surface word, as used for lookups.
    pub fn norm(&self, word: &str) -> String {
        let irregular: HashMap<&str, &str> = IRREGULAR.iter().copied().collect();
        self.normalise(word, &irregular)
    }
}

/// The English words or phrases an entry can be written as: split on ` / `,
/// drop parenthetical notes and a verb's leading `to`.
fn english_keys(english: &str, kind: &str) -> Vec<String> {
    let mut keys = Vec::new();
    for alt in english.split(" / ") {
        let mut text = String::new();
        let mut depth = 0;
        for c in alt.chars() {
            match c {
                '(' => depth += 1,
                ')' if depth > 0 => depth -= 1,
                _ if depth == 0 => text.push(c),
                _ => {}
            }
        }
        let lower = text.to_lowercase();
        let words: Vec<&str> = lower
            .split_whitespace()
            .map(|w| w.trim_matches(|c: char| !c.is_alphanumeric() && c != '\''))
            .filter(|w| !w.is_empty())
            .collect();
        let words = match words.split_first() {
            Some((&"to", rest)) if kind == "verb" && !rest.is_empty() => rest.to_vec(),
            _ => words,
        };
        if !words.is_empty() {
            keys.push(words.join(" "));
        }
    }
    keys
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_split_alternatives_and_drop_notes() {
        assert_eq!(english_keys("to look for / search", "verb"), ["look for", "search"]);
        assert_eq!(english_keys("to be (essential)", "verb"), ["be"]);
        assert_eq!(english_keys("OK / fine", "phrase"), ["ok", "fine"]);
        assert_eq!(english_keys("rock (music)", "noun"), ["rock"]);
    }
}
