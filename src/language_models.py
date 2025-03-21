import openai
from openai import OpenAI
import os
import time
import torch
import gc
from typing import Dict, List
import random
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from src.config import OPENAI_API_KEY, OPENAI_BASE_URL


class LanguageModel():
    def __init__(self, model_name):
        self.model_name = model_name

    def batched_generate(self, prompts_list: List, max_n_tokens: int, temperature: float):
        """
        Generates responses for a batch of prompts using a language model.
        """
        raise NotImplementedError


class HuggingFace(LanguageModel):
    MAX_ERROR_RETRY = 50

    def __init__(self, model_name, model, tokenizer):
        super(HuggingFace, self).__init__(model_name)
        self.model = model
        self.tokenizer = tokenizer

        self.tokenizer.pad_token = self.tokenizer.eos_token

        self.eos_token_ids = [self.tokenizer.eos_token_id]

    def batched_generate(self,
                         full_prompts_list,
                         max_n_tokens: int,
                         temperature: float,
                         top_p: float = 1.0, ):
        
        processed_prompts = []
        for prompt in full_prompts_list:
            if isinstance(prompt, dict) and 'prompt' in prompt:
                processed_prompts.append(prompt['prompt'])
            elif isinstance(prompt, str):
                processed_prompts.append(prompt)
            else:
                processed_prompts.append(str(prompt))
        
        inputs = self.tokenizer(processed_prompts, return_tensors='pt', padding=True)
        inputs = {k: v.to(self.model.device.index) for k, v in inputs.items()}

        for i in range(self.MAX_ERROR_RETRY):
            try:
                # Batch generation
                if temperature > 0:
                    output_ids = self.model.generate(
                        **inputs,
                        max_new_tokens=max_n_tokens,
                        do_sample=True,
                        temperature=temperature,
                        eos_token_id=self.eos_token_ids,
                        top_p=top_p,
                    )
                else:
                    output_ids = self.model.generate(
                        **inputs,
                        max_new_tokens=max_n_tokens,
                        do_sample=False,
                        eos_token_id=self.eos_token_ids,
                        top_p=1,
                        temperature=1,  # To prevent warning messages
                    )
                break
            except Exception as e:
                print(type(e), e)
                continue

        # If the model is not an encoder-decoder type, slice off the input tokens
        if not self.model.config.is_encoder_decoder:
            output_ids = output_ids[:, inputs["input_ids"].shape[1]:]

        # Batch decoding
        outputs_list = self.tokenizer.batch_decode(output_ids, skip_special_tokens=True)

        for key in inputs:
            inputs[key].to('cpu')
        output_ids.to('cpu')
        del inputs, output_ids
        gc.collect()
        torch.cuda.empty_cache()

        outputs_list = [output.strip() for output in outputs_list]
        print("here's the output list:", outputs_list)
        return outputs_list

    def extend_eos_tokens(self):
        # Add closing braces for Vicuna/Llama eos when using attacker model
        self.eos_token_ids.extend([
            self.tokenizer.encode("}")[1],
            29913,
            9092,
            16675])


class GPT(LanguageModel):
    API_RETRY_SLEEP = 10
    API_QUERY_SLEEP = 0.5
    API_MAX_RETRY = 10
    API_TIMEOUT = 60

    def __init__(self, model_name, seed=None):
        super().__init__(model_name)
        self.seed = seed

    def generate(self,
                 conv: List[Dict],
                 max_n_tokens: int,
                 temperature: float,
                 top_p: float):
        '''
        Args:
            conv: List of dictionaries, OpenAI API format
            max_n_tokens: int, max number of tokens to generate
            temperature: float, temperature for sampling
            top_p: float, top p for sampling
        Returns:
            str: generated response
        '''
        success = False
        output = ""

        for _ in range(self.API_MAX_RETRY):
            try:
                client = OpenAI(
                    api_key=OPENAI_API_KEY,
                    base_url=OPENAI_BASE_URL
                )
                completions = client.chat.completions.create(
                    model=self.model_name,
                    messages=conv,
                    max_tokens=max_n_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    n=1,
                    stop=None,
                    stream=True,
                    seed=self.seed,
                    timeout=self.API_TIMEOUT,
                )
                for chunk in completions:
                    delta = chunk.choices[0].delta
                    if chunk.choices[0].delta.content is not None:
                        output += delta.content
                success = True
                break
            except Exception as e:
                print(type(e), e)
                time.sleep(self.API_RETRY_SLEEP)
                continue

        time.sleep(self.API_QUERY_SLEEP)
        if not success:
            print('!!! Fail to generate: ')
            print(conv)

        return output

    def batched_generate(self,
                         convs_list: List[List[Dict]],
                         max_n_tokens: int,
                         temperature: float,
                         top_p: float = 1.0):
        partial_generate = partial(self.generate, 
                                   max_n_tokens=max_n_tokens, 
                                   temperature=temperature, 
                                   top_p=top_p
                                   )
        with ThreadPoolExecutor(max_workers=len(convs_list)) as executor:
            results = list(executor.map(partial_generate, convs_list))

        return results
