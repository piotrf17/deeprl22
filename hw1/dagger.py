#!/usr/bin/env python

"""
Code to load an expert policy and generate roll-out data for behavioral cloning.
Example usage:
    python run_expert.py experts/Humanoid-v1.pkl Humanoid-v1 --render \
            --num_rollouts 20

Author of this script and included expert policies: Jonathan Ho (hoj@openai.com)
"""
import os

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

import gym
import h5py
import load_policy
from matplotlib import pyplot
import numpy as np
import os
import progressbar
import tensorflow as tf
import tf_util

import pdb

def simulate(envname, max_timesteps, num_rollouts, policy_fn, render=False):
    with tf.Session():
        tf_util.initialize()
    
        env = gym.make(envname)
        max_steps = max_timesteps or env.spec.timestep_limit

        returns = []
        observations = []
        actions = []
        progress = progressbar.ProgressBar()
        for i in progress(range(num_rollouts)):
            obs = env.reset()
            done = False
            totalr = 0.
            steps = 0
            while not done:
                action = policy_fn(obs[None,:])
                observations.append(obs)
                actions.append(action)
                obs, r, done, _ = env.step(action)
                totalr += r
                steps += 1
                if render:
                    env.render()
                if steps >= max_steps:
                    break
            returns.append(totalr)

        return observations, actions, returns


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('expert_policy_file', type=str)
    parser.add_argument('envname', type=str)
    parser.add_argument("--validation_file", type=str, default=None)
    parser.add_argument("--train_file", type=str, default=None)
    parser.add_argument("--output_file", type=str, default=None)    
    parser.add_argument("--max_timesteps", type=int)    
    parser.add_argument('--num_rollouts', type=int, default=20,
                        help='Number of test rollouts.')
    args = parser.parse_args()

    f = h5py.File(args.train_file, 'r')
    train_observations = np.array(f['observations'])
    train_actions = np.array(f['actions'])
    train_returns = np.array(f['returns'])
    f.close()

    f = h5py.File(args.validation_file, 'r')
    validation_observations = np.array(f['observations'])
    validation_actions = np.array(f['actions'])
    f.close()

    tf.logging.set_verbosity(tf.logging.ERROR)
    
    no = len(train_observations[0])
    nu = len(train_actions[0])
    N = len(train_observations)
    graph = tf.Graph()
    with graph.as_default():
      train_inputs = tf.placeholder(tf.float32, shape=(None, no))
      train_outputs = tf.placeholder(tf.float32, shape=(None, nu))

      connection_widths = [no, 1000, nu]
      apply_relu = [False, True]
      train_layer = train_inputs
      eval_layer = train_inputs
      for i in range(len(connection_widths) - 1):
          A = tf.Variable(tf.truncated_normal([
              connection_widths[i],
              connection_widths[i + 1]
          ], stddev=1.0))
          b = tf.Variable(tf.truncated_normal([
              1, connection_widths[i + 1],
          ], stddev=1.0))

          if apply_relu[i]:
              train_layer = tf.nn.tanh(train_layer)
              eval_layer = tf.nn.tanh(eval_layer)
          train_layer = tf.matmul(tf.nn.dropout(train_layer, 0.8), A) + b
          eval_layer = tf.matmul(eval_layer, A) + b          

      loss = tf.nn.l2_loss(train_layer - train_outputs)
      eval_loss = tf.nn.l2_loss(eval_layer - train_outputs)      
      global_step = tf.Variable(0)  # Count the number of steps taken.
      #learning_rate = tf.train.exponential_decay(0.5, global_step, 100, 0.95)
      learning_rate = 0.001  # For hopper.
      optimizer = tf.train.AdamOptimizer(learning_rate).minimize(
          loss, global_step=global_step)

    pyplot.ion()
    N = 1000
    with tf.Session(graph=graph) as session:
        policy_fn = load_policy.load_policy(args.expert_policy_file)
        
        tf.global_variables_initializer().run()
        num_epochs = 5000
        batch_size = 1000
        progress = progressbar.ProgressBar()
        losses = []
        for epoch in progress(range(num_epochs)):
          _loss = 0.0
          N = train_observations.shape[0]
          sel = np.random.choice(range(N), batch_size)
          m = 5 * int(np.ceil((1.0 * N) / batch_size))
          for _ in range(m):
              feed_dict = {
                  train_inputs: train_observations[sel, :],
                  train_outputs: train_actions[sel, :],
              }
              _, _loss, _eval_loss, _eval_layer = session.run([optimizer, loss, eval_loss, eval_layer], feed_dict=feed_dict)
          if epoch % 100 == 99:
              losses.append(_eval_loss)
              if len(losses) > 1:
                  pyplot.figure(22)
                  pyplot.cla()
                  pyplot.semilogy(losses)
                  pyplot.show()
                  pyplot.pause(0.001)
                  print 'train loss: %f' % losses[-1]
                  feed_dict = {
                      train_inputs: validation_observations,
                      train_outputs: validation_actions,
                  }
                  validation_loss, _eval_layer, = session.run([eval_loss, eval_layer], feed_dict=feed_dict)
                  print 'validation loss: %f' % _eval_loss

          if epoch > 1000 and epoch % 100 == 99:
              def trained_policy_fn(obs):
                  action = session.run([eval_layer], feed_dict={
                      train_inputs: obs,
                      train_outputs: [[0.0 for _ in range(nu)]]
                  })
                  return action

              observations, _, returns = simulate(
                  args.envname, args.max_timesteps, 1, trained_policy_fn)

              train_observations = np.vstack((
                  train_observations, observations
              ))

              actions = np.array([policy_fn(obs.reshape((1, no))) for obs in observations]).squeeze()
              train_actions = np.vstack((
                  train_actions, actions
              ))

        def trained_policy_fn(obs):
            action = session.run([eval_layer], feed_dict={
                train_inputs: obs,
                train_outputs: [[0.0 for _ in range(nu)]]
            })
            return action

        observations, _, returns = simulate(
            args.envname, args.max_timesteps, args.num_rollouts, trained_policy_fn,
            render=True)
              
        #print('expert returns', train_returns)
        print('expert mean return', np.mean(train_returns))
        print('expert std of return', np.std(train_returns))
        
        #print('returns', returns)
        print('mean return', np.mean(returns))
        print('std of return', np.std(returns))

        f=h5py.File(args.output_file, 'w')
        f.create_dataset('losses', data=losses)
        f.create_dataset('returns', data=returns)
        f.close()
        

if __name__ == '__main__':
    main()