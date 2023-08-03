import torch
from types import SimpleNamespace

from rl.datasets.replay_buffer import ReplayBuffer
from rl.agents.dqn.dqn_network import DQNNetwork
from rl.envs.environment import EnvironmentSpec
from rl.utils.logging import Logger
from rl.agents.dqn.dqn_learner import DQNLearner


class DDQNLearner(DQNLearner):

    def __init__(self,
                 config: SimpleNamespace,
                 logger: Logger,
                 environment_spec: EnvironmentSpec,
                 network: DQNNetwork,
                 buffer: ReplayBuffer):

        super(DDQNLearner, self).__init__(config, logger, environment_spec, network, buffer)

    def update(self, total_n_timesteps: int, total_n_episodes: int):
        if len(self.buffer) < self.config.warmup_step: return False
        if not self.buffer.can_sample(batch_size=self.config.batch_size): return False

        # Optimize policy for K epochs
        for i in range(self.config.gradient_steps):
            # Sampling batch from replay buffer
            sample_batched = self.buffer.sample(self.config.batch_size)

            state = sample_batched["state"]
            action = sample_batched["action"]
            reward = sample_batched["reward"]
            next_state = sample_batched["next_state"]
            done = sample_batched["done"]

            # Critic Update
            with torch.no_grad():
                next_q_values = self.critic(next_state)
                next_action = next_q_values.argmax(dim=1, keepdim=True)
                next_q_values = self.target_critic(next_state)
                next_max_q_value = next_q_values.gather(1, next_action)
                target_q_value = reward + (1 - done) * self.config.gamma * next_max_q_value  # target bootstrapping

            q_values = self.critic(state)    # current q value
            q_value = q_values.gather(1, action.type(torch.int64))
            value_loss = self.loss(target_q_value, q_value)

            max_Q = torch.max(target_q_value, axis=0).values.cpu().numpy()[0]   # for monitoring

            self.optimizer.zero_grad()
            value_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.critic.parameters(), self.config.grad_norm_clip)
            self.optimizer.step()

            self.learner_step += 1

            # soft update target
            if self.config.target_update_type == "soft": self.network.soft_update_target()

            # Performance Logging
            self.logger.log_stat("value_loss", value_loss.item(), total_n_timesteps)
            self.logger.log_stat("max_Q", max_Q, total_n_timesteps)

        # hard update target
        if (self.config.target_update_type == "hard" and
           (total_n_timesteps - self.last_target_update_step) >= self.config.target_update_interval):
            self.network.hard_update_target()
            self.last_target_update_step = total_n_timesteps

        if self.config.lr_annealing:
            self.critic_lr_scheduler.step(total_n_timesteps)
            self.logger.log_stat("critic learning rate", self.optimizer.param_groups[0]['lr'], total_n_timesteps)

        return True