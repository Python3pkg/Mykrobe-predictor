from Bio import SeqIO
from Bio.Seq import Seq
from copy import copy 
import itertools
from collections import Counter
import logging
import datetime
import math 

def unique(seq):
    seen = set()
    seen_add = seen.add
    return [ x for x in seq if x not in seen and not seen_add(x)]


class Variant(object):

    def __init__(self, ref, pos, alt):
        self.ref = ref 
        self.pos = pos
        self.alt = alt

    def __str__(self):
        return "".join([self.ref, str(self.pos), self.alt])

    def __repr__(self):
        return "".join([self.ref, str(self.pos), self.alt])

    @property
    def length(self):
        return abs(len(self.ref) - len(self.alt))

    @property
    def is_indel(self):
        """ Return whether or not the variant is an INDEL """
        if len(self.ref) > 1:
            return True
        if self.alt is None:
            return True
        elif len(self.alt) != len(self.ref):
            return True
        return False

    @property
    def is_deletion(self):
        """ Return whether or not the INDEL is a deletion """
        if self.is_indel:
            # just one alt allele
            if self.alt is None:
                return True
            if len(self.ref) > len(self.alt):
                return True
            else:
                return False
        else:
            return False 

    @property 
    def is_insertion(self):
        if self.is_indel:
            if self.alt is None:
                return False
            if len(self.alt) > len(self.ref):
                return True
            else:
                return False
        else:
            return False  

class AlleleGenerator(object):
    """docstring for PanelGenerator"""
    def __init__(self, reference_filepath, kmer = 31):
        self.reference_filepath = reference_filepath
        self.kmer = kmer
        self.ref = []
        self.ref_length = 0
        self._read_reference()

    def _read_reference(self):
        for record in SeqIO.parse(self.reference_filepath, 'fasta'):
            self.ref += list(record.seq)
        self.ref_length = len(self.ref)

    def create(self, v, context = []):
        ## Position should be 1 based
        self._check_valid_variant(v)
        context = self._remove_contexts_spanning_del(v, context)
        context = self._remove_contexts_not_within_k(v, context)
        wild_type_reference = self._get_wildtype_reference(v.pos)
        alternates = self._generate_alternates_on_all_backgrounds(v,  context)
        return Panel(wild_type_reference, v.pos, alternates)

    def _check_valid_variant(self, v):
        index = v.pos - 1
        if "".join(self.ref[index:(index + len(v.ref))]) != v.ref:
            raise ValueError("""Cannot create alleles as ref at pos %i is not %s 
                                (it's %s) are you sure you're using one-based
                                co-ordinates?
                             """ % (v.pos, v.ref, "".join(self.ref[index: (index + len(v.ref))])))
        if v.pos <= 0 :
            raise ValueError("Position should be 1 based")  

    def _remove_contexts_spanning_del(self, v, context):
        """If there's a var within the range of an DEL remove from context"""
        if v.is_deletion:
            ran = range(v.pos, v.pos + len(v.ref))
            context = [c for c in context if not c.pos in ran]
        return context

    def _remove_contexts_not_within_k(self, v, context):
        new_context = []
        for c in context:
            if c.is_insertion:
                effective_pos = c.pos - c.length
            elif c.is_deletion:
                effective_pos = c.pos + c.length
            else:
                effective_pos = c.pos
            if abs(v.pos - effective_pos) < self.kmer:
                new_context.append(c)
        return new_context


    def _get_wildtype_reference(self, pos):
        i, start_index, end_index = self._get_start_end(pos)        
        return self._get_reference_segment(start_index, end_index)    

    def _get_reference_segment(self, start_index, end_index):
        return copy(self.ref[start_index:end_index])

    def _get_alternate_reference_segment(self, v, context):
        ref_segment_length_delta = self._calculate_length_delta_from_indels(v, context)
        i, start_index, end_index = self._get_start_end(v.pos, delta = ref_segment_length_delta)
        return self._get_reference_segment(start_index, end_index)        

    def _generate_alternates_on_all_backgrounds(self, v, context):
        ## First, create all the context combinations
        context_combinations = self._get_all_context_combinations(context)
        ## For each context, create the background and alternate
        alternates = []
        for context_combo in context_combinations:
            ref_segment_length_delta = self._calculate_length_delta_from_indels(v, context_combo)
            i, start_index, end_index = self._get_start_end(v.pos, delta = ref_segment_length_delta)
            alternate_reference_segment = self._get_reference_segment(start_index, end_index)
            background = self._generate_background_using_context(i, v, alternate_reference_segment, context_combo)
            alternate = copy(background)
            i -=  self._calculate_length_delta_from_variant_list([c for c in context_combo if c.pos <= v.pos and c.is_indel])              
            assert "".join(alternate[i:(i + len(v.ref))]) == v.ref               
            alternate[i : i + len(v.ref)] = v.alt
            alternates.append(alternate)
        return alternates

    def _get_all_context_combinations(self, context):
        context_list = [[]]
        if context:
            contexts = self._create_multiple_contexts(context) ## Create contexts if multiple variants at given posision
            for context in contexts:
                context_combinations = self._get_combinations_of_backgrounds(context)
                context_list.extend(context_combinations)
        return context_list

    def _generate_background_using_context(self, i, v, alternate_reference_segment, context ):
        backgrounds = [alternate_reference_segment]
        new_background = copy(alternate_reference_segment)
        for variant in context:
            j = i + variant.pos - v.pos
            assert "".join(new_background[j : j+ len(variant.ref)]) == variant.ref                        
            new_background[j : j + len(variant.ref)] = variant.alt
        return new_background

    def _create_multiple_contexts(self, context):
        new_contexts = self._recursive_context_creator([context])
        return new_contexts

    def _recursive_context_creator(self, contexts):
        ## This is only run when there are multiple variants at the same position
        valid = True
        for i,context in enumerate(contexts):
            position_counts = Counter([v.pos for v in context])
            if not all([c == 1 for c in position_counts.values()]):
                valid = False
        if valid:
            return contexts
        else:
            for i,context in enumerate(contexts):
                position_counts = Counter([v.pos for v in context])
                if not all([c == 1 for c in position_counts.values()]):
                    position = position_counts.most_common(1)[0][0]
                    repeated_vars = []
                    common_vars = []
                    for var in context:
                        if var.pos == position:
                            repeated_vars.append(var)
                        else:
                            common_vars.append(var)
                    new_contexts = []
                    for var in repeated_vars:
                        context = [var] + common_vars
                        new_contexts.append(context)
                    contexts.pop(i)
                    contexts += new_contexts
                    self._recursive_context_creator(contexts)
        return contexts

    def _get_combinations_of_backgrounds(self, context):
        combination_context = []
        for L in range(1, len(context)+1):
          for subset in itertools.combinations(context, L):
            combination_context.append(list(subset))
        return combination_context

    def _get_start_end(self, pos, delta = 0):
        start_delta = math.floor(delta / 2)
        end_delta = math.ceil(delta / 2)
        start_index = pos - self.kmer - start_delta
        end_index =  pos + self.kmer + end_delta + 1
        i = self.kmer - 1 + start_delta
        if start_index < 0:
            diff = abs(start_index)
            start_index = 0 
            end_index += diff
            i -= diff
        elif end_index > self.ref_length:
            diff = abs(end_index - self.ref_length)
            end_index = self.ref_length
            start_index -= diff
            i += diff
        return (i, start_index, end_index)

    def _calculate_length_delta_from_indels(self, v, context):
        """Calculates the change in required bases for given variant.
        For deletions we need extra bases to get same length flanks"""
        variants = context + [v]
        return self._calculate_length_delta_from_variant_list(variants)

    def _calculate_length_delta_from_variant_list(self, variants):
        deletions_length = [v.length for v in variants if v.is_deletion]
        insertions_length = [v.length for v in variants if v.is_insertion]
        return sum(deletions_length) - sum(insertions_length)        

class Panel(object):

    def __init__(self, ref, pos, alts):
        self.ref = "".join(ref)
        self.pos = pos
        self.alts = unique(["".join(alt) for alt in alts])

        